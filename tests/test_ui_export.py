import json
from pathlib import Path

from mirror import observe as observe_mod
from mirror import ui_export as ui_mod

REPO = Path(__file__).resolve().parent.parent


def test_export_graph_shape(store, mock):
    observe_mod.observe(store, mock, "s1", {
        "text": "피곤한 저녁에 익숙한 집에 배달을 주문했다.",
        "occurred_at": "2026-10-16T20:00:00+09:00"}, 0)
    data = ui_mod.export_graph(store)
    assert data["nodes"] and data["edges"] and data["hypotheses"]
    assert set(data) >= {"nodes", "edges", "hypotheses",
                         "report_markdown", "generated_at"}
    hyp = data["hypotheses"][0]
    assert set(hyp) >= {"id", "statement", "status", "use_policy",
                        "supports", "contradicts", "alternatives"}
    assert hyp["supports"] and hyp["supports"][0]["text"]
    json.dumps(data, ensure_ascii=False)  # 직렬화 가능


def test_ui_page_exists():
    page = REPO / "ui" / "index.html"
    assert page.exists()
    text = page.read_text(encoding="utf-8")
    assert "mirror-graph.json" in text
    assert "cytoscape" in text.lower()
