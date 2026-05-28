"""Fixture loader for influencer adapter tests.

Fixture files live in tests/influencers/fixtures/ and are committed to the repo
so unit tests stay deterministic without network.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

FIXTURES = Path(__file__).parent / "fixtures"


def load_text(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


def load_json(name: str) -> dict:
    return json.loads(load_text(name))


@pytest.fixture
def fixture_text():
    return load_text


@pytest.fixture
def fixture_json():
    return load_json
