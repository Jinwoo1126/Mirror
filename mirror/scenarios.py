"""개인 시나리오: 문서 생성, 음악 추천, 하루 계획, 선택 도우미.

시나리오 = 입력 양식 + 내 기록에서 뽑은 맥락 + 생성 지침.
LLM이 있으면 지침과 맥락으로 생성하고, 없으면 기록으로 만들 수 있는 것
(구성안, 후보 정렬, 규칙 기반 계획)만 돌려준다. 곡 제목이나 본문을
지어내지 않는다. 쓴 근거는 관찰 ID로 함께 돌려준다.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable

from . import assistant as assistant_mod
from . import profile as profile_mod
from . import retrieve as retrieve_mod
from . import schema
from .store import MirrorStore

Generate = Callable[[str], tuple[str, str]]  # prompt -> (mode, text)

RULES = ("규칙: 사용자의 성격이나 유형을 단정하지 않는다. 기록에 없는 사실을 "
         "지어내지 않는다. 기록을 쓰면 관찰 ID로 표시한다.")


@dataclass
class Field:
    key: str
    label: str
    kind: str = "text"            # text | textarea | select
    placeholder: str = ""
    options: list[str] = field(default_factory=list)
    required: bool = False


@dataclass
class Scenario:
    id: str
    name: str
    summary: str
    fields: list[Field]
    needs_llm: str                # 로컬 결과가 무엇인지 사용자에게 알리는 문구


def _lines(value: str) -> list[str]:
    return [ln.strip(" -•\t") for ln in (value or "").splitlines()
            if ln.strip(" -•\t")]


def _context(store, index, query: str, budget: int = 6) -> list[dict]:
    out = retrieve_mod.retrieve(store, query, [], None, budget, index=index)
    return out.get("items", [])


def _cite(items: list[dict]) -> list[str]:
    ids: list[str] = []
    for it in items:
        if it["kind"] == schema.OBSERVATION:
            ids.append(it["id"])
        else:
            ids += it.get("supports", [])
    return sorted(set(ids))


def _items_block(items: list[dict]) -> str:
    lines = []
    for it in items:
        if it["kind"] == schema.OBSERVATION:
            lines.append(f"- 관찰 {it['id']}: {it['text']}")
        else:
            lines.append(f"- 가설 {it['id']} [{it['status']}]: "
                         f"{it['statement']} (근거 {','.join(it['supports'])})")
    return "\n".join(lines) or "(관련 기록 없음)"


def _style_lines(prof: dict) -> list[str]:
    st = prof["writing"]["stats"]
    if not st.get("sentences"):
        return ["저장된 글이 없어서 문체 지침을 만들 수 없습니다."]
    top = st["endings"][0]
    return [f"문장 평균 {st['avg_sentence_chars']}자, 가장 긴 문장 "
            f"{st['longest_sentence_chars']}자",
            f"어미는 {top['name']}가 {round(top['ratio'] * 100)}% "
            f"(관찰 {st['observations']}건 기준)"]


# -- 시나리오 정의 -------------------------------------------------------------

SCENARIOS: dict[str, Scenario] = {s.id: s for s in (
    Scenario(
        "doc_draft", "문서 생성",
        "내 문체로 블로그 글, 업무 메모, 메일, 일기 초안을 만듭니다.",
        [Field("topic", "주제", "text", "예: 이번 주 DJ 선곡 과정", required=True),
         Field("kind", "종류", "select",
               options=["블로그 글", "업무 메모", "메일", "일기"]),
         Field("points", "꼭 넣을 내용", "textarea", "한 줄에 하나씩")],
        "본문 생성은 LLM 연결이 필요합니다. 지금은 구성안과 문체 지침, 쓸 수 있는 "
        "내 기록을 돌려줍니다."),
    Scenario(
        "music_recommend", "음악 추천",
        "상황에 맞는 곡 후보를 내 기록 기준으로 고릅니다.",
        [Field("situation", "상황", "text", "예: 야근 후 집에 가는 길", required=True),
         Field("candidates", "후보 곡 (선택)", "textarea",
               "한 줄에 하나씩. 넣으면 내 기록 기준으로 정렬합니다"),
         Field("avoid", "피하고 싶은 것", "text")],
        "새 곡을 찾아 주는 일은 LLM 연결이 필요합니다. 후보를 넣으면 내 기록 "
        "기준으로 정렬하고, 없으면 추천 기준만 돌려줍니다."),
    Scenario(
        "day_plan", "하루 계획",
        "오늘 할 일을 내 상태와 기록에 맞게 정리합니다.",
        [Field("state", "지금 상태", "select", options=["보통", "피곤함", "개운함"]),
         Field("tasks", "할 일", "textarea", "한 줄에 하나씩", required=True)],
        "규칙 기반으로 정리합니다. LLM이 있으면 시간 배분 설명까지 붙습니다."),
    Scenario(
        "choice_help", "선택 도우미",
        "메뉴, 장소, 영화처럼 고르기 어려운 선택을 내 기록과 함께 봅니다.",
        [Field("request", "무엇을 고르나요", "text", "예: 저녁 주문", required=True),
         Field("options", "선택지", "textarea", "두 개 이상, 한 줄에 하나씩",
               required=True)],
        "관점별 예측을 나란히 둡니다. 결정은 사용자가 합니다."),
)}


def list_scenarios() -> list[dict]:
    return [schema.to_jsonable(s) for s in SCENARIOS.values()]


# -- 실행 ---------------------------------------------------------------------

class ScenarioError(ValueError):
    pass


def _require(inputs: dict, *keys: str) -> None:
    missing = [k for k in keys if not str(inputs.get(k, "")).strip()]
    if missing:
        raise ScenarioError("필수 입력이 비었습니다: " + ", ".join(missing))


def _doc(store, index, prof, inputs, generate):
    _require(inputs, "topic")
    topic = inputs["topic"].strip()
    kind = inputs.get("kind") or "블로그 글"
    points = _lines(inputs.get("points", ""))
    items = _context(store, index, topic)
    style = _style_lines(prof)
    examples = [i["text"] for i in items if i["kind"] == schema.OBSERVATION][:2]
    prompt = (f"{kind} 초안을 써라.\n주제: {topic}\n"
              + ("꼭 넣을 내용:\n" + "\n".join(f"- {p}" for p in points) + "\n"
                 if points else "")
              + "내 문체 지침:\n" + "\n".join(f"- {s}" for s in style) + "\n"
              + ("내가 쓴 글 예시:\n" + "\n---\n".join(examples) + "\n"
                 if examples else "")
              + f"관련 기록:\n{_items_block(items)}\n{RULES}\n"
              "출력은 완성된 초안 본문만 쓴다. 문체 지침의 수치나 검증 메모는 "
              "설명하지 말고 따르기만 한다. 기록에 없는 내용은 지어내지 말고 "
              "[직접 채우기: 무엇을]로 자리만 표시한다.")
    mode, text = generate(prompt)
    if mode != "local" and text:
        return mode, text, items
    outline = {
        "블로그 글": ["도입: 왜 이 주제를 다루는지", "본론 1", "본론 2", "정리와 다음 계획"],
        "업무 메모": ["목적", "현황", "결정 또는 요청 사항", "다음 일정"],
        "메일": ["인사와 용건", "배경", "요청 내용", "마무리"],
        "일기": ["오늘 한 일", "느낀 점", "내일 할 일"],
    }.get(kind, ["도입", "본론", "정리"])
    lines = [f"[{kind}] {topic}", "", "구성안"]
    for i, sec in enumerate(outline, 1):
        lines.append(f"{i}. {sec}")
    if points:
        lines += ["", "꼭 넣을 내용"] + [f"- {p}" for p in points]
    lines += ["", "내 문체 지침"] + [f"- {s}" for s in style]
    mats = [i for i in items if i["kind"] == schema.OBSERVATION]
    if mats:
        lines += ["", "쓸 수 있는 내 기록"]
        lines += [f"- [{(m.get('occurred_at') or '')[:10]}] {m['text']} ({m['id']})"
                  for m in mats[:4]]
    return "local", "\n".join(lines), items


def _music(store, index, prof, inputs, generate):
    _require(inputs, "situation")
    situation = inputs["situation"].strip()
    avoid = (inputs.get("avoid") or "").strip()
    cands = _lines(inputs.get("candidates", ""))
    items = _context(store, index, f"{situation} 음악 곡 DJ 선곡", budget=6)
    prefs = [p["target"] for p in prof["preferences"]]
    prompt = (f"상황에 맞는 곡을 추천하라. 상황: {situation}\n"
              + (f"피할 것: {avoid}\n" if avoid else "")
              + (("후보 중에서만 고르고 이유를 한 줄씩 써라:\n"
                  + "\n".join(f"- {c}" for c in cands) + "\n") if cands else "")
              + f"내 선호: {', '.join(prefs) or '기록 없음'}\n"
              + f"관련 기록:\n{_items_block(items)}\n{RULES}")
    mode, text = generate(prompt)
    if mode != "local" and text:
        return mode, text, items
    lines = []
    basis = " ".join([situation] + prefs
                     + [i["statement"] for i in items if i["kind"] == schema.HYPOTHESIS])
    if len(cands) >= 2:
        scorer = index.score if index is not None else (
            lambda q, c: len(set(retrieve_mod.tokenize(q))
                             & set(retrieve_mod.tokenize(c))))
        ranked = sorted(((scorer(basis, c), c) for c in cands
                         if not avoid or avoid not in c), reverse=True)
        lines.append("내 기록 기준 정렬 (점수가 높을수록 상황·선호와 가깝습니다)")
        lines += [f"{n}. {c}  ({s:.2f})" for n, (s, c) in enumerate(ranked, 1)]
        lines.append("")
    else:
        lines += ["후보 곡이 없어서 순위를 만들 수 없습니다.",
                  "곡 제목을 두 개 이상 넣어주면 내 기록 기준으로 정렬합니다.", ""]
    lines.append("추천 기준 (내 기록에서)")
    lines.append(f"- 선호: {', '.join(prefs)}" if prefs else "- 저장된 음악 선호가 없습니다.")
    for it in items:
        if it["kind"] == schema.HYPOTHESIS:
            lines.append(f"- {it['statement']} (근거 {len(it['supports'])}건)")
    n_obs = sum(1 for i in items if i["kind"] == schema.OBSERVATION)
    if n_obs < 3:
        lines.append(f"음악과 관련된 기록이 {n_obs}건뿐이라 순위의 의미가 약합니다.")
    return "local", "\n".join(lines), items


def _plan(store, index, prof, inputs, generate):
    _require(inputs, "tasks")
    tasks = _lines(inputs["tasks"])
    state = inputs.get("state") or "보통"
    items = _context(store, index, f"{state} 하루 계획 할 일 {' '.join(tasks[:3])}")
    # 검색 점수에 기대지 않고 저장된 가설에서 직접 찾는다
    avoid_hyp = None
    for t in prof["tendencies"]:
        if "피곤" in t["statement"] and "회피" in t["statement"]:
            avoid_hyp = {"kind": schema.HYPOTHESIS, "id": t["id"],
                         "statement": t["statement"], "status": t["status"],
                         "supports": store.supporting_observations(t["id"])}
            items = items + [avoid_hyp]
            break
    prompt = (f"오늘 할 일을 정리하라. 상태: {state}\n"
              + "\n".join(f"- {t}" for t in tasks)
              + f"\n관련 기록:\n{_items_block(items)}\n{RULES}")
    mode, text = generate(prompt)
    if mode != "local" and text:
        return mode, text, items
    lines = [f"상태: {state}"]
    if state == "피곤함" and avoid_hyp is not None:
        lines += [f"기록에서 반복된 것: {avoid_hyp['statement']} "
                  f"(근거 {len(avoid_hyp['supports'])}건)",
                  "그래서 오늘은 고를 일을 줄입니다.", "", "오늘 할 일 (최대 3개)"]
        lines += [f"{n}. {t}" for n, t in enumerate(tasks[:3], 1)]
        if tasks[3:]:
            lines += ["", "내일로 미루기"] + [f"- {t}" for t in tasks[3:]]
    else:
        lines += ["", "오늘 할 일 (입력한 순서)"]
        lines += [f"{n}. {t}" for n, t in enumerate(tasks, 1)]
    return "local", "\n".join(lines), items


def _choice(store, index, prof, inputs, generate, router):
    _require(inputs, "request", "options")
    options = _lines(inputs["options"])
    if len(options) < 2:
        raise ScenarioError("선택지는 두 개 이상 필요합니다.")
    out = assistant_mod.answer(store, router, None, inputs["request"].strip(),
                               index=index, options=options)
    mode, text = generate(out["prompt"])
    if mode == "local" or not text:
        text = assistant_mod.extractive_answer(out["retrieval"], out.get("proposal"))
        mode = "local"
    return mode, text, out["retrieval"]


def run_scenario(store: MirrorStore, router, index, scenario_id: str,
                 inputs: dict, generate: Generate) -> dict:
    scenario = SCENARIOS.get(scenario_id)
    if scenario is None:
        raise ScenarioError(f"알 수 없는 시나리오: {scenario_id}")
    prof = profile_mod.build_profile(store)
    if scenario_id == "choice_help":
        mode, text, items = _choice(store, index, prof, inputs, generate, router)
    else:
        fn = {"doc_draft": _doc, "music_recommend": _music,
              "day_plan": _plan}[scenario_id]
        mode, text, items = fn(store, index, prof, inputs, generate)
    return {"scenario": scenario_id, "mode": mode, "text": text,
            "citations": _cite(items), "note": scenario.needs_llm if mode == "local" else ""}
