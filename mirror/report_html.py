"""실행 결과 시각화 문서 생성 (공유용 단일 HTML, 외부 의존 없음).

실행: python -m mirror.report_html --out mirror-results.html
"""

from __future__ import annotations

import argparse
import html
import json
import os
import re
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
ACCENT = "#0f766e"
INK = "#1c1917"
MUTED = "#57534e"
COLORS = {"hit": "#0f766e", "miss": "#dc2626", "abstain": "#d6d3d1",
          "exact": "#0f766e", "partial": "#f59e0b", "missing": "#dc2626",
          "spurious": "#a8a29e"}


def esc(text) -> str:
    return html.escape(str(text if text is not None else ""))


def hbar(rows: list[tuple[str, float, float]], width: int = 640,
         unit: str = "") -> str:
    """[(라벨, 값, 최대)] 수평 막대."""
    rh, top = 26, 8
    height = top * 2 + rh * len(rows)
    out = [f'<svg viewBox="0 0 {width} {height}" width="100%" role="img">']
    for i, (label, value, maximum) in enumerate(rows):
        y = top + i * rh
        w = (width - 260) * (value / maximum) if maximum else 0
        out.append(f'<text x="0" y="{y + 17}" font-size="12" fill="{INK}">'
                   f'{esc(label)}</text>')
        out.append(f'<rect x="180" y="{y}" width="{w:.0f}" height="18" '
                   f'fill="{ACCENT}" rx="3"/>')
        out.append(f'<text x="{190 + w:.0f}" y="{y + 15}" font-size="12" '
                   f'fill="{MUTED}">{value:g}{unit}</text>')
    out.append("</svg>")
    return "".join(out)


def grouped_bars(groups: dict[str, dict[str, int]], keys: list[str],
                 width: int = 640) -> str:
    names = list(groups)
    bw, gap = 22, 14
    gw = bw * len(keys) + gap
    height, top, bottom = 220, 10, 30
    maximum = max((v for g in groups.values() for v in g.values()),
                  default=1)
    out = [f'<svg viewBox="0 0 {width} {height}" width="100%" role="img">']
    for gi, name in enumerate(names):
        x0 = 60 + gi * (gw + 40)
        for ki, key in enumerate(keys):
            value = groups[name].get(key, 0)
            h = (height - top - bottom) * value / maximum if maximum else 0
            x = x0 + ki * bw
            out.append(f'<rect x="{x}" y="{height - bottom - h:.0f}" '
                       f'width="{bw - 3}" height="{h:.0f}" '
                       f'fill="{COLORS.get(key, ACCENT)}" rx="2">'
                       f'<title>{esc(name)} {esc(key)}: {value}</title></rect>')
            if value:
                out.append(f'<text x="{x}" y="{height - bottom - h - 4:.0f}" '
                           f'font-size="11" fill="{MUTED}">{value}</text>')
        out.append(f'<text x="{x0}" y="{height - 10}" font-size="13" '
                   f'fill="{INK}">{esc(name)}</text>')
    lx = 60 + len(names) * (gw + 40)
    for ki, key in enumerate(keys):
        out.append(f'<rect x="{lx}" y="{20 + ki * 22}" width="12" height="12" '
                   f'fill="{COLORS.get(key, ACCENT)}"/>')
        out.append(f'<text x="{lx + 17}" y="{30 + ki * 22}" font-size="12" '
                   f'fill="{MUTED}">{esc(key)}</text>')
    out.append("</svg>")
    return "".join(out)


def stacked_bars(rows: list[tuple[str, dict[str, int]]],
                 keys: list[str], width: int = 640) -> str:
    rh, top = 30, 8
    height = top + rh * (len(rows) + 1) + 10
    out = [f'<svg viewBox="0 0 {width} {height}" width="100%" role="img">']
    for i, (label, parts) in enumerate(rows):
        y = top + i * rh
        total = sum(parts.get(k, 0) for k in keys) or 1
        out.append(f'<text x="0" y="{y + 17}" font-size="12" fill="{INK}">'
                   f'{esc(label)}</text>')
        x = 150
        for key in keys:
            w = (width - 170) * parts.get(key, 0) / total
            if w > 0:
                out.append(f'<rect x="{x:.0f}" y="{y}" width="{w:.0f}" '
                           f'height="18" fill="{COLORS.get(key, ACCENT)}">'
                           f'<title>{esc(key)}: {parts.get(key, 0)}</title>'
                           f'</rect>')
            x += w
    lx = 150
    for key in keys:
        out.append(f'<rect x="{lx}" y="{top + len(rows) * rh}" width="12" '
                   f'height="12" fill="{COLORS.get(key, ACCENT)}"/>')
        out.append(f'<text x="{lx + 17}" y="{top + len(rows) * rh + 11}" '
                   f'font-size="12" fill="{MUTED}">{esc(key)}</text>')
        lx += 110
    out.append("</svg>")
    return "".join(out)


def collect_pytest() -> dict:
    proc = subprocess.run(
        [sys.executable, "-m", "pytest", "-v", "--tb=no"],
        cwd=REPO, capture_output=True, text=True, timeout=600)
    passed, failed, errors, per_file = 0, 0, 0, {}
    for line in proc.stdout.splitlines():
        match = re.match(r"(tests/\S+\.py)::\S+ (PASSED|FAILED|ERROR)", line)
        if match:
            per_file.setdefault(match.group(1),
                                {"passed": 0, "failed": 0})["passed" if
                match.group(2) == "PASSED" else "failed"] += 1
            if match.group(2) == "PASSED":
                passed += 1
            elif match.group(2) == "FAILED":
                failed += 1
            else:
                errors += 1
    duration = ""
    match = re.search(r"in ([\d.]+s)", proc.stdout)
    if match:
        duration = match.group(1)
    return {"passed": passed, "failed": failed, "errors": errors,
            "per_file": per_file, "duration": duration,
            "exit_code": proc.returncode}


def collect_replay() -> dict:
    from mirror import replay as replay_mod

    db = tempfile.mkdtemp() + "/results-replay.kuzu"
    report = replay_mod.run_replay(str(REPO / "eval" / "dj_episodes.jsonl"),
                                   db_path=db)
    return report


def collect_eval() -> dict:
    from mirror import eval_precision as ev
    from mirror import extract as ex

    return ev.score_file(str(REPO / "eval" / "gold_observations.jsonl"),
                         ex.get_extractor(os.environ.get("MIRROR_EXTRACTOR", "spark")))


def collect_search() -> dict:
    from mirror import search as search_mod
    from mirror import vstore as vstore_mod
    from mirror.store import MirrorStore

    db = str(REPO / "data" / "local" / "mirror.kuzu")
    if not Path(db).exists():
        return {"available": False}
    store = MirrorStore(db)
    try:
        embedder = vstore_mod.get_embedder()
        base = str(Path(db).with_suffix(""))
        idx = search_mod.HybridIndex.build(
            store, vector_path=base + ".vectors.json",
            bm25_path=base + ".bm25.json", embedder=embedder)
        pairs = [
            ("피곤한 저녁에 익숙한 집에 배달을 주문했다", "지친 밤에 단골집에 또 갔다"),
            ("피곤한 저녁에 익숙한 집에 배달을 주문했다", "개운한 아침에 새로운 메뉴를 시도했다"),
            ("피곤한 저녁에 익숙한 집에 배달을 주문했다", "블로그에 DJ 선곡 과정을 정리했다"),
        ]
        sims = []
        vecs = embedder.embed([t for pair in pairs for t in pair])
        if vecs is not None:
            for i in range(0, len(vecs), 2):
                sims.append(round(vstore_mod.cosine(vecs[i], vecs[i + 1]), 4))
        hits = idx.search_obs("지친 밤 단골집", store, top_k=3)
        demo = [{"text": h["obs"].text, "score": h["score"],
                 "bm25": h["bm25"], "vec": h["vec"]} for h in hits]
        return {"available": True, "mode": idx.mode,
                "pairs": [{"a": a[:24], "b": b[:24], "sim": s}
                          for (a, b), s in zip(pairs, sims)],
                "demo_query": "지친 밤 단골집", "demo": demo}
    finally:
        store.close()


def collect_onto() -> dict:
    from mirror.store import MirrorStore

    db = str(REPO / "data" / "local" / "mirror.kuzu")
    if not Path(db).exists():
        return {"available": False}
    store = MirrorStore(db)
    try:
        vocab = store.relation_vocabulary()
        entities = []
        for row in store._rows("MATCH (e:Entity) RETURN e.name, e.kind, "
                               "e.description LIMIT 6"):
            entities.append({"name": row[0], "kind": row[1],
                             "description": (row[2] or "")[:160]})
        hyps = []
        for row in store._rows("MATCH (h:Hypothesis) RETURN h.statement, "
                               "h.status LIMIT 10"):
            hyps.append({"statement": row[0], "status": row[1]})
        return {"available": True, "vocabulary": vocab,
                "entities": entities, "hypotheses": hyps}
    finally:
        store.close()


def collect_graph() -> dict:
    from mirror import ui_export as ui_mod
    from mirror.store import MirrorStore

    db = str(REPO / "data" / "local" / "mirror.kuzu")
    if not Path(db).exists():
        return {"nodes": [], "edges": []}
    store = MirrorStore(db)
    try:
        data = ui_mod.export_graph(store)
        return {"nodes": [{"id": n["id"], "kind": n["kind"],
                           "label": n["label"][:28]} for n in data["nodes"]],
                "edges": [{"s": e["source"], "t": e["target"],
                           "label": (e.get("phrase") or e["rel"])[:14]
                           if e["rel"] == "RELATED_TO" else e["rel"]}
                          for e in data["edges"]]}
    finally:
        store.close()


def collect_report() -> str:
    from mirror import report as report_mod
    from mirror.store import MirrorStore

    db = str(REPO / "data" / "local" / "mirror.kuzu")
    if not Path(db).exists():
        return "(데모 DB 없음)"
    store = MirrorStore(db)
    try:
        return report_mod.weekly_report(store, "2000-01-01T00:00:00+00:00")[
            "markdown"]
    finally:
        store.close()


PAGE = """<!DOCTYPE html>
<html lang="ko">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Mirror 실행 결과 리포트</title>
<style>
body {{ margin: 0; color: #1c1917; background: #fff;
  font-family: -apple-system, BlinkMacSystemFont, "Apple SD Gothic Neo", "Noto Sans KR", sans-serif; }}
.wrap {{ max-width: 860px; margin: 0 auto; padding: 28px 22px 60px; }}
h1 {{ font-size: 26px; margin: 0 0 4px; }}
.sub {{ color: #57534e; font-size: 13px; margin-bottom: 18px; }}
.badges {{ display: flex; gap: 8px; flex-wrap: wrap; margin-bottom: 8px; }}
.badge {{ background: #f5f5f4; border: 1px solid #e7e5e4; border-radius: 8px;
  padding: 6px 12px; font-size: 13px; }}
.badge b {{ font-size: 16px; }}
nav.toc {{ background: #f5f5f4; border-radius: 8px; padding: 10px 16px; margin: 16px 0 8px; }}
nav.toc a {{ color: #0f766e; font-size: 13px; margin-right: 14px; text-decoration: none; }}
h2 {{ font-size: 19px; margin: 34px 0 6px; padding-top: 10px; border-top: 1px solid #e7e5e4; }}
p {{ font-size: 14px; line-height: 1.65; }}
table {{ width: 100%; border-collapse: collapse; font-size: 13px; margin: 10px 0; }}
th, td {{ border-bottom: 1px solid #e7e5e4; padding: 6px 8px; text-align: left; }}
th {{ background: #f5f5f4; }}
code {{ background: #f5f5f4; padding: 1px 5px; border-radius: 4px; font-size: 12px; }}
pre {{ background: #f5f5f4; padding: 12px; border-radius: 8px; font-size: 12px;
  white-space: pre-wrap; }}
#g {{ width: 100%; height: 480px; border: 1px solid #e7e5e4; border-radius: 8px; }}
.note {{ font-size: 13px; color: #57534e; }}
.ok {{ color: #0f766e; font-weight: bold; }}
.bad {{ color: #dc2626; font-weight: bold; }}
</style>
</head>
<body>
<div class="wrap">
<h1>Mirror 실행 결과 리포트</h1>
<p class="sub">생성 {generated_at} · 단일 파일, 외부 의존 없음</p>
<div class="badges">{badges}</div>
<nav class="toc">
<a href="#tests">테스트</a><a href="#replay">비교 실험</a><a href="#eval">추출 정밀도</a>
<a href="#search">하이브리드 검색</a><a href="#onto">동적 온톨로지</a>
<a href="#graph">그래프</a><a href="#report">주간 리포트</a>
</nav>
<h2 id="tests">1. 테스트 스위트</h2>
<p>{pytest_summary}</p>
{pytest_chart}
{pytest_table}
<h2 id="replay">2. 비교 실험 (A vs M1 vs M2, 10 에피소드)</h2>
<p>{replay_summary}</p>
{replay_chart}
{replay_table}
<h2 id="eval">3. 추출 정밀도 (정답셋 4장면)</h2>
<p>{eval_summary}</p>
{eval_chart}
<h2 id="search">4. 하이브리드 검색 실측</h2>
<p>{search_summary}</p>
{search_chart}
{search_demo}
<h2 id="onto">5. 동적 온톨로지</h2>
<p>고정 구조 뼈대 위에 쌓인 관계 서술 어휘와 엔티티 요약입니다.</p>
{onto_vocab}
{onto_entities}
<h2 id="graph">6. 지식 그래프</h2>
<p class="note">노드를 드래그할 수 있습니다. {graph_note}</p>
<svg id="g" role="img"></svg>
<h2 id="report">7. 주간 변경 리포트</h2>
<pre>{weekly_md}</pre>
</div>
<script>
const GRAPH = __GRAPH_JSON__;
(function() {{
  const svg = document.getElementById("g");
  const W = 860, H = 480;
  svg.setAttribute("viewBox", `0 0 ${{W}} ${{H}}`);
  const colors = {{ Observation: "#7dd3fc", Entity: "#fcd34d", Context: "#c4b5fd",
    Preference: "#6ee7b7", Criterion: "#fca5a5", Hypothesis: "#fdba74",
    Style: "#e7e5e4", Correction: "#f87171" }};
  const nodes = GRAPH.nodes.map((n, i) => ({{ ...n,
    x: W/2 + 180*Math.cos(i/GRAPH.nodes.length*6.283),
    y: H/2 + 150*Math.sin(i/GRAPH.nodes.length*6.283), vx: 0, vy: 0 }}));
  const byId = Object.fromEntries(nodes.map(n => [n.id, n]));
  const edges = GRAPH.edges.filter(e => byId[e.s] && byId[e.t]);
  const NS = "http://www.w3.org/2000/svg";
  function el(tag, attrs, parent) {{
    const e = document.createElementNS(NS, tag);
    for (const k in attrs) e.setAttribute(k, attrs[k]);
    parent.appendChild(e);
    return e;
  }}
  const gE = el("g", {{}}, svg), gN = el("g", {{}}, svg);
  const edgeEls = edges.map(e => el("line", {{ stroke: "#a8a29e",
    "stroke-width": 1 }}, gE));
  const nodeEls = nodes.map(n => {{
    const g = el("g", {{}}, gN);
    el("circle", {{ r: 9, fill: colors[n.kind] || "#e7e5e4",
      stroke: "#57534e", "stroke-width": 1 }}, g).appendChild(
      (() => {{ const t = document.createElementNS(NS, "title");
        t.textContent = n.kind + ": " + n.label; return t; }})());
    const t = el("text", {{ "font-size": 8, "text-anchor": "middle",
      y: 22, fill: "#44403c" }}, g);
    t.textContent = n.label.slice(0, 14);
    g.addEventListener("mousedown", ev => {{
      const move = me => {{
        const r = svg.getBoundingClientRect();
        n.x = (me.clientX - r.left) / r.width * W;
        n.y = (me.clientY - r.top) / r.height * H;
        n.vx = 0; n.vy = 0; tick(1);
      }};
      const up = () => {{ window.removeEventListener("mousemove", move);
        window.removeEventListener("mouseup", up); }};
      window.addEventListener("mousemove", move);
      window.addEventListener("mouseup", up);
      ev.preventDefault();
    }});
    return g;
  }});
  function tick() {{
    for (let i = 0; i < nodes.length; i++) for (let j = i+1; j < nodes.length; j++) {{
      const a = nodes[i], b = nodes[j];
      let dx = a.x - b.x, dy = a.y - b.y;
      const d2 = Math.max(dx*dx + dy*dy, 400);
      const f = 2600 / d2;
      const d = Math.sqrt(d2);
      dx /= d; dy /= d;
      a.vx += dx*f; a.vy += dy*f; b.vx -= dx*f; b.vy -= dy*f;
    }}
    edges.forEach(e => {{
      const a = byId[e.s], b = byId[e.t];
      const dx = b.x - a.x, dy = b.y - a.y;
      const d = Math.max(Math.sqrt(dx*dx + dy*dy), 1);
      const f = (d - 70) * 0.02;
      a.vx += dx/d*f; a.vy += dy/d*f; b.vx -= dx/d*f; b.vy -= dy/d*f;
    }});
    nodes.forEach(n => {{
      n.vx += (W/2 - n.x) * 0.004; n.vy += (H/2 - n.y) * 0.004;
      n.vx *= 0.85; n.vy *= 0.85;
      n.x = Math.min(W-20, Math.max(20, n.x + n.vx));
      n.y = Math.min(H-20, Math.max(20, n.y + n.vy));
    }});
  }}
  function draw() {{
    edges.forEach((e, i) => {{
      const a = byId[e.s], b = byId[e.t], l = edgeEls[i];
      l.setAttribute("x1", a.x); l.setAttribute("y1", a.y);
      l.setAttribute("x2", b.x); l.setAttribute("y2", b.y);
    }});
    nodes.forEach((n, i) => nodeEls[i].setAttribute(
      "transform", `translate(${{n.x}},${{n.y}})`));
  }}
  let frames = 0;
  (function frame() {{ tick(); draw(); if (++frames < 240) requestAnimationFrame(frame); }})();
}})();
</script>
</body>
</html>
"""


def build() -> str:
    print("collect: pytest ...", flush=True)
    pytest = collect_pytest()
    print("collect: replay ...", flush=True)
    replay = collect_replay()
    print("collect: eval ...", flush=True)
    evaluation = collect_eval()
    print("collect: search ...", flush=True)
    search = collect_search()
    print("collect: onto/graph/report ...", flush=True)
    onto = collect_onto()
    graph = collect_graph()
    weekly_md = collect_report()

    total = pytest["passed"] + pytest["failed"] + pytest["errors"]
    badges = (
        f'<span class="badge">테스트 <b class="ok">{pytest["passed"]}/{total}</b>'
        f' 통과 ({esc(pytest["duration"])})</span>')
    for name, stats in replay["agents"].items():
        acc = stats["accuracy"]
        badges += (f'<span class="badge">{esc(name)} 정확도 '
                   f'<b>{acc if acc is not None else "-"}</b> '
                   f'({stats["hits"]}/{stats["predicted"]})</span>')
    totals = evaluation["totals"]
    badges += (f'<span class="badge">추출 정밀도 '
               f'<b>{totals["exact_precision"]}</b></span>')

    pytest_summary = (
        f"총 {total}개 중 통과 {pytest['passed']}개, 실패 {pytest['failed']}개, "
        f"에러 {pytest['errors']}개 ({esc(pytest['duration'])}). "
        "exit code " + ("0" if pytest["exit_code"] == 0 else str(pytest["exit_code"])) + ".")
    pytest_chart = hbar(
        [(f.split("/")[-1], v["passed"], v["passed"] + v["failed"])
         for f, v in sorted(pytest["per_file"].items())],
        unit="개")
    pytest_table = ("<table><tr><th>파일</th><th>통과</th><th>실패</th></tr>"
                    + "".join(
                        f"<tr><td><code>{esc(f)}</code></td><td>{v['passed']}"
                        f"</td><td>{v['failed']}</td></tr>"
                        for f, v in sorted(pytest["per_file"].items()))
                    + "</table>")

    ra = replay["agents"]
    replay_summary = (
        f"10개 에피소드 시간순 리플레이. A {ra['A']['hits']}/{ra['A']['predicted']} "
        f"(정확도 {ra['A']['accuracy']}), M1 {ra['M1']['hits']}/{ra['M1']['predicted']} "
        f"(정확도 {ra['M1']['accuracy']}), M2 {ra['M2']['hits']}/{ra['M2']['predicted']} "
        f"(정확도 {ra['M2']['accuracy']}). {esc(replay['note'])}")
    replay_chart = grouped_bars(
        {name: {"hit": s["hits"], "miss": s["misses"],
                "abstain": s["abstains"]} for name, s in ra.items()},
        ["hit", "miss", "abstain"])
    rows = []
    for ep in ra["A"]["episodes"]:
        cells = [f"<td><code>{esc(ep['episode'])}</code></td>"]
        for name in ra:
            entry = next(e for e in ra[name]["episodes"]
                         if e["episode"] == ep["episode"])
            choice = entry.get("choice")
            mark = "보류" if choice is None else esc(choice)
            cells.append(f"<td>{mark}</td>")
        rows.append("<tr>" + "".join(cells) + "</tr>")
    replay_table = ("<table><tr><th>에피소드</th>"
                    + "".join(f"<th>{esc(n)}</th>" for n in ra) + "</tr>"
                    + "".join(rows) + "</table>")

    eval_summary = (
        f"정답 {totals['gold']}개 중 정답 {totals['exact']}·부분 {totals['partial']}·"
        f"누락 {totals['missing']}, 예측 {totals['predicted']}개 중 오류 "
        f"{totals['spurious']} (정밀도 {totals['exact_precision']}, 재현율 "
        f"{totals['exact_recall']}). 오류 유형: "
        + ", ".join(f"{k} {v}" for k, v in
                     totals.get("error_types", {}).items()))
    eval_chart = stacked_bars(
        [(s["source_id"], {"exact": s["exact"], "partial": s["partial"],
                           "missing": s["missing"], "spurious": s["spurious"]})
         for s in evaluation["scenes"]],
        ["exact", "partial", "missing", "spurious"])

    if search.get("available"):
        search_summary = (
            f"모드: {esc(search['mode'])}. 한국어 의미 유사도 실측 — "
            + "; ".join(f"{esc(p['a'])}↔{esc(p['b'])} = {p['sim']}"
                        for p in search["pairs"])
            + ". 값이 높을수록 의미가 가깝다.")
        search_chart = hbar([(f"{p['a']}↔{p['b']}", p["sim"], 1.0)
                              for p in search["pairs"]])
        search_demo = ("<p>질의 <code>" + esc(search["demo_query"])
                       + "</code> 상위 결과:</p><table><tr><th>텍스트</th><th>"
                         "RRF</th><th>BM25</th><th>벡터</th></tr>"
                       + "".join(
                           f"<tr><td>{esc(h['text'][:60])}</td>"
                           f"<td>{h['score']}</td><td>{h['bm25']}</td>"
                           f"<td>{h['vec']}</td></tr>" for h in search["demo"])
                       + "</table>")
    else:
        search_summary = "데모 DB가 없어 검색 실측을 건너뛰었습니다."
        search_chart, search_demo = "", ""

    if onto.get("available"):
        onto_vocab = ("<table><tr><th>서술</th><th>횟수</th><th>예시</th></tr>"
                      + "".join(
                          f"<tr><td>{esc(v['phrase'])}</td><td>{v['count']}"
                          f"</td><td><code>{esc(v['examples'][0]['from'][:12])}"
                          f" → {esc(v['examples'][0]['to'][:12])}</code></td>"
                          f"</tr>" for v in onto["vocabulary"])
                      + "</table>") if onto["vocabulary"] else "<p>아직 관계가 없습니다.</p>"
        onto_entities = ("<table><tr><th>엔티티</th><th>종류</th><th>요약</th></tr>"
                         + "".join(
                             f"<tr><td>{esc(e['name'])}</td>"
                             f"<td>{esc(e['kind'])}</td>"
                             f"<td>{esc(e['description'])}</td></tr>"
                             for e in onto["entities"]) + "</table>")
    else:
        onto_vocab, onto_entities = "<p>데모 DB가 없습니다.</p>", ""

    graph_note = (f"노드 {len(graph['nodes'])}개, 엣지 {len(graph['edges'])}개.")
    page = PAGE.format(
        generated_at=datetime.now(timezone.utc).astimezone().strftime(
            "%Y-%m-%d %H:%M %Z"),
        badges=badges, pytest_summary=pytest_summary,
        pytest_chart=pytest_chart, pytest_table=pytest_table,
        replay_summary=replay_summary, replay_chart=replay_chart,
        replay_table=replay_table, eval_summary=eval_summary,
        eval_chart=eval_chart, search_summary=search_summary,
        search_chart=search_chart, search_demo=search_demo,
        onto_vocab=onto_vocab, onto_entities=onto_entities,
        graph_note=graph_note, weekly_md=esc(weekly_md))
    return page.replace("__GRAPH_JSON__", json.dumps(graph, ensure_ascii=False))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Mirror results HTML")
    parser.add_argument("--out", default="mirror-results.html")
    args = parser.parse_args(argv)
    html_text = build()
    with open(args.out, "w", encoding="utf-8") as f:
        f.write(html_text)
    print(f"wrote {args.out} ({len(html_text)} bytes)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
