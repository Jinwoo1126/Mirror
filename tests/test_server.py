import asyncio
import json

import pytest

from mirror import server


@pytest.fixture
def isolated_server(tmp_path, monkeypatch):
    monkeypatch.setenv("MIRROR_DB", str(tmp_path / "mcp.kuzu"))
    monkeypatch.setenv("MIRROR_EXTRACTOR", "mock")
    monkeypatch.setenv("MIRROR_NO_INDEX", "1")
    server._store = None
    server._extractor = None
    server._index = None
    yield server
    if server._store is not None:
        server._store.close()
    server._store = None
    server._extractor = None
    server._index = None


def _payload(result):
    text = "".join(getattr(c, "text", "") for c in result.content)
    return json.loads(text)


def test_tools_registered(isolated_server):
    tools = asyncio.run(isolated_server.mcp.list_tools())
    assert {"mirror_observe", "mirror_control",
            "mirror_retrieve"} <= {t.name for t in tools}


def test_mcp_observe_control_retrieve_roundtrip(isolated_server):
    mcp = isolated_server.mcp
    obs = asyncio.run(mcp.call_tool("mirror_observe", {
        "source_id": "s1",
        "event": {"text": "피곤한 저녁에 익숙한 집에 배달을 주문했다."},
        "base_revision": 0,
    }))
    data = _payload(obs)
    assert data["ok"] and data["revision"] == 1

    query = asyncio.run(mcp.call_tool("mirror_control", {
        "action": "query",
        "target": {"kind": "Observation", "id": data["obs_id"]},
    }))
    assert _payload(query)["ok"]

    found = asyncio.run(mcp.call_tool("mirror_retrieve", {
        "request": "피곤한 저녁 주문", "budget": 5,
    }))
    assert _payload(found)["items"]
