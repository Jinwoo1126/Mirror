"""Mirror 패키지 (P1 그래프 MVP + P2 가설 엔진 + P3 비교 실험 + P4 제안·UI).

CLI 진입점(ingest, eval_precision, replay, server, ui_export)은
`python -m` 이중 임포트를 피하려고 eager import하지 않는다.
`from mirror import replay`처럼 직접 임포트해서 쓴다.
"""

from . import (
    agents,
    assistant,
    control,
    extract,
    hypotheses,
    llm,
    observe,
    policy,
    report,
    retrieve,
    router,
    schema,
    simulate,
    store,
)

__all__ = [
    "agents", "assistant", "control", "extract", "hypotheses",
    "llm", "observe", "policy", "report", "retrieve", "router",
    "schema", "simulate", "store",
]
