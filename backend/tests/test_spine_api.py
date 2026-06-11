"""SP1 MCP/v2 端到端(mock scrape,不联网)。"""
from unittest.mock import patch

from app.db import SessionLocal, init_db


def _scrape_stub(db, url, **kw):
    return {"scrape_id": "scr_x", "url": url,
            "data": {"title": "MockItem", "confidence": 0.95},
            "metadata": {"canonical": None}, "html": "<html>m</html>",
            "warnings": [], "usage": {"source": "live", "credits_used": 2}}


def test_crawl_custom_source_tool():
    init_db()
    from app import mcp_server
    with patch("app.spine._do_scrape", side_effect=_scrape_stub):
        out = mcp_server.crawl_custom_source(
            url="https://x.com/p/1", dataset="mcp-set", save_policy="main")
    assert out["record_id"] and out["quality_status"] == "main"
    assert out["provenance"]["source_url"] == "https://x.com/p/1"


def test_query_dataset_tool():
    init_db()
    from app import mcp_server
    with patch("app.spine._do_scrape", side_effect=_scrape_stub):
        mcp_server.crawl_custom_source(url="https://x.com/p/2",
                                       dataset="mcp-q", save_policy="main")
    out = mcp_server.query_dataset(dataset="mcp-q", query="MockItem")
    assert out["total"] >= 1
