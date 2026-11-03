"""Mirror MCP 서버 (계획서 6장).

호스트는 실행, Mirror는 이해. 세 개의 문:
  mirror_retrieve — 관련 이해 조회
  mirror_observe  — 관찰 수집
  mirror_control  — 조회·정정·사용 중지·삭제·재허용 (추론 없이 직접 적용)

실행: MIRROR_DB=data/local/mirror.kuzu python -m mirror.server
"""

from __future__ import annotations

import os

from mcp.server.mcpserver import MCPServer

from . import control as control_mod
from . import extract as ex
from . import observe as observe_mod
from . import retrieve as retrieve_mod
from . import schema
from .store import MirrorStore

mcp = MCPServer("mirror")

_store: MirrorStore | None = None
_extractor: ex.Extractor | None = None
_index = None


def get_store() -> MirrorStore:
    global _store
    if _store is None:
        _store = MirrorStore(os.environ.get("MIRROR_DB", "data/local/mirror.kuzu"))
    return _store


def get_index():
    global _index
    if _index is None and os.environ.get("MIRROR_NO_INDEX") != "1":
        from . import search as search_mod

        db = os.environ.get("MIRROR_DB", "data/local/mirror.kuzu")
        base = os.path.splitext(db)[0]
        _index = search_mod.HybridIndex.build(
            get_store(), vector_path=base + ".vectors.json",
            bm25_path=base + ".bm25.json")
    return _index


def get_extractor() -> ex.Extractor:
    global _extractor
    if _extractor is None:
        _extractor = ex.get_extractor(os.environ.get("MIRROR_EXTRACTOR", "spark"))
    return _extractor


@mcp.tool()
def mirror_retrieve(request: str, explicit_conditions: list | None = None,
                    as_of: str | None = None, budget: int = 10,
                    options: list | None = None) -> dict:
    """관련 사실·가설·스타일을 근거 관찰 ID·반례·use_policy와 함께 조회.

    options(선택지)가 있으면 시뮬레이션+개입 제안(proposal)도 첨부한다.
    """
    return retrieve_mod.retrieve(get_store(), request,
                                 explicit_conditions or [], as_of, budget,
                                 options=options, index=get_index())


@mcp.tool()
def mirror_observe(source_id: str, event: dict, base_revision: int) -> dict:
    """관찰 1건을 수집. 중복 여부·반영된 변경·새 revision 또는 실패 사유 반환."""
    res = observe_mod.observe(get_store(), get_extractor(), source_id, event,
                              base_revision)
    if res.ok and not res.duplicate and get_index() is not None:
        get_index().refresh(get_store())
    return schema.to_jsonable(res)  # type: ignore[arg-type]


@mcp.tool()
def mirror_control(action: str, target: dict | None = None,
                   payload: dict | None = None) -> dict:
    """조회·정정·사용 중지·삭제·재허용. 저장소에 바로 적용."""
    res = control_mod.control(get_store(), action, target, payload or {})
    if res.ok and action != "query" and get_index() is not None:
        get_index().refresh(get_store())
    return schema.to_jsonable(res)  # type: ignore[arg-type]


def main() -> None:
    mcp.run()


if __name__ == "__main__":
    main()
