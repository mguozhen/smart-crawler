from __future__ import annotations

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

import app.db as db_mod
from app.auth import hash_password, verify_password
from app.db import Base
from app.models import User


pytestmark = pytest.mark.unit


def _bind_memory_db(monkeypatch):
    """Point app.db.session_scope at a fresh in-memory SQLite db."""
    engine = create_engine("sqlite:///:memory:", future=True)
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    monkeypatch.setattr(db_mod, "SessionLocal", Session)
    return Session


def test_seed_does_not_overwrite_ui_changed_admin_password(monkeypatch):
    """A password changed in the UI must survive the next deploy/restart.

    ADMIN_PASSWORD seeds the account once; re-running the seeder must not
    clobber a password the admin later set themselves.
    """
    Session = _bind_memory_db(monkeypatch)
    monkeypatch.setenv("ADMIN_USERNAME", "admin")
    monkeypatch.setenv("ADMIN_PASSWORD", "InitialPass1")

    # First boot creates the admin with the env password.
    db_mod._seed_users()
    db = Session()
    admin = db.query(User).filter(User.username == "admin").first()
    assert verify_password("InitialPass1", admin.password_hash)

    # Admin changes their password through the UI.
    admin.password_hash = hash_password("UserChosen2")
    db.commit()

    # Next deploy runs the seeder again with the same env var still set.
    db_mod._seed_users()

    db2 = Session()
    admin2 = db2.query(User).filter(User.username == "admin").first()
    assert verify_password("UserChosen2", admin2.password_hash), (
        "seeder clobbered the UI-changed password on restart")
    assert not verify_password("InitialPass1", admin2.password_hash)


def test_seed_force_reset_restores_env_password(monkeypatch):
    """Explicit opt-in recovery path can still reset a lost admin password."""
    Session = _bind_memory_db(monkeypatch)
    monkeypatch.setenv("ADMIN_USERNAME", "admin")
    monkeypatch.setenv("ADMIN_PASSWORD", "InitialPass1")
    db_mod._seed_users()

    db = Session()
    admin = db.query(User).filter(User.username == "admin").first()
    admin.password_hash = hash_password("UserChosen2")
    db.commit()

    # Operator forgot the password and opts into a reset.
    monkeypatch.setenv("ADMIN_PASSWORD", "Recovered3")
    monkeypatch.setenv("ADMIN_PASSWORD_FORCE_RESET", "1")
    db_mod._seed_users()

    db2 = Session()
    admin2 = db2.query(User).filter(User.username == "admin").first()
    assert verify_password("Recovered3", admin2.password_hash)
