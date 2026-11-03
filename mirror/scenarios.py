"""Personal scenarios: document drafts, music picks, day plans, choice help.

A scenario is an input form, context pulled from the person's memories, and
generation instructions. With an LLM it generates from those instructions and
context. Without one it returns only what the memories can support (an outline,
a ranking of the candidates the person supplied, a rule-based plan). It never
invents track titles or body text. The memories it used come back as ids.
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

RULES = ("Rules: never label the person's personality or type. Do not invent "
         "facts that are not in the memories. When you use a memory, cite its id. "
         "Write in the same language as the request.")

# state values the day-plan rule treats as "tired" (UI value and stored Korean value)
TIRED_STATES = ("Tired", "피곤함")
TIRED_WORDS = ("피곤", "tired", "fatigue", "exhaust")
REST_WORDS = ("회피", "쉬", "휴식", "avoid", "rest", "skip", "fewer")


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
    needs_llm: str                # tells the person what a local result means


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
            lines.append(f"- memory {it['id']}: {it['text']}")
        else:
            lines.append(f"- hypothesis {it['id']} [{it['status']}]: "
                         f"{it['statement']} (evidence {','.join(it['supports'])})")
    return "\n".join(lines) or "(no related memories)"


def _style_lines(prof: dict) -> list[str]:
    st = prof["writing"]["stats"]
    if not st.get("sentences"):
        return ["No saved writing yet, so there is no style guide."]
    top = st["endings"][0]
    return [f"Average sentence {st['avg_sentence_chars']} characters, longest "
            f"{st['longest_sentence_chars']}",
            f"Sentence endings: {top['name']} {round(top['ratio'] * 100)}% "
            f"(counted from {st['observations']} memories)"]


# -- scenario definitions ------------------------------------------------------

DOC_KINDS = ["Blog post", "Work memo", "Email", "Journal entry"]

SCENARIOS: dict[str, Scenario] = {s.id: s for s in (
    Scenario(
        "doc_draft", "Write a document",
        "Draft a blog post, work memo, email or journal entry in your own style.",
        [Field("topic", "Topic", "text", "e.g. How I picked this week's DJ set",
               required=True),
         Field("kind", "Type", "select", options=DOC_KINDS),
         Field("points", "Points to include", "textarea", "One per line")],
        "Writing the full text needs an LLM connection. For now you get an outline, "
        "a style guide and the memories you can draw on."),
    Scenario(
        "music_recommend", "Pick music",
        "Choose tracks that fit the moment, based on what Mirror knows about you.",
        [Field("situation", "Situation", "text", "e.g. Walking home after a late shift",
               required=True),
         Field("candidates", "Candidate tracks (optional)", "textarea",
               "One per line. If you add some, Mirror ranks them for you"),
         Field("avoid", "Anything to avoid", "text")],
        "Finding new tracks needs an LLM connection. With candidates, they are ranked "
        "from your memories. Without, you only get the criteria."),
    Scenario(
        "day_plan", "Plan my day",
        "Sort today's tasks to fit your state and what Mirror has learned.",
        [Field("state", "How do you feel", "select", options=["Normal", "Tired", "Fresh"]),
         Field("tasks", "Tasks", "textarea", "One per line", required=True)],
        "Planned by rules. With an LLM connected, it also explains the time split."),
    Scenario(
        "choice_help", "Help me choose",
        "Weigh menus, places or movies against your own history.",
        [Field("request", "What are you choosing", "text", "e.g. Tonight's dinner order",
               required=True),
         Field("options", "Options", "textarea", "Two or more, one per line",
               required=True)],
        "Shows predictions from different viewpoints side by side. You decide."),
)}


def list_scenarios() -> list[dict]:
    return [schema.to_jsonable(s) for s in SCENARIOS.values()]


# -- running -------------------------------------------------------------------

class ScenarioError(ValueError):
    pass


def _require(inputs: dict, *keys: str) -> None:
    missing = [k for k in keys if not str(inputs.get(k, "")).strip()]
    if missing:
        raise ScenarioError("Required input is empty: " + ", ".join(missing))


def _doc(store, index, prof, inputs, generate):
    _require(inputs, "topic")
    topic = inputs["topic"].strip()
    kind = inputs.get("kind") or DOC_KINDS[0]
    points = _lines(inputs.get("points", ""))
    items = _context(store, index, topic)
    style = _style_lines(prof)
    examples = [i["text"] for i in items if i["kind"] == schema.OBSERVATION][:2]
    prompt = (f"Write a draft of this {kind}.\nTopic: {topic}\n"
              + ("Points to include:\n" + "\n".join(f"- {p}" for p in points) + "\n"
                 if points else "")
              + "My style guide:\n" + "\n".join(f"- {s}" for s in style) + "\n"
              + ("Examples of my writing:\n" + "\n---\n".join(examples) + "\n"
                 if examples else "")
              + f"Related memories:\n{_items_block(items)}\n{RULES}\n"
              "Output only the finished draft. Follow the style guide without "
              "explaining its numbers or adding review notes. Do not make up content "
              "that is not in the memories; mark the gap as [fill in: what].")
    mode, text = generate(prompt)
    if mode != "local" and text:
        return mode, text, items
    outline = {
        "Blog post": ["Opening: why this topic", "Main point 1", "Main point 2",
                      "Wrap-up and next steps"],
        "Work memo": ["Purpose", "Current status", "Decision or request", "Next dates"],
        "Email": ["Greeting and reason for writing", "Background", "Request", "Closing"],
        "Journal entry": ["What I did today", "How I felt", "What is next"],
    }.get(kind, ["Opening", "Body", "Wrap-up"])
    lines = [f"[{kind}] {topic}", "", "Outline"]
    for i, sec in enumerate(outline, 1):
        lines.append(f"{i}. {sec}")
    if points:
        lines += ["", "Points to include"] + [f"- {p}" for p in points]
    lines += ["", "My style guide"] + [f"- {s}" for s in style]
    mats = [i for i in items if i["kind"] == schema.OBSERVATION]
    if mats:
        lines += ["", "Memories you can draw on"]
        lines += [f"- [{(m.get('occurred_at') or '')[:10]}] {m['text']} ({m['id']})"
                  for m in mats[:4]]
    return "local", "\n".join(lines), items


def _music(store, index, prof, inputs, generate):
    _require(inputs, "situation")
    situation = inputs["situation"].strip()
    avoid = (inputs.get("avoid") or "").strip()
    cands = _lines(inputs.get("candidates", ""))
    items = _context(store, index, f"{situation} music track DJ set", budget=6)
    prefs = [p["target"] for p in prof["preferences"]]
    prompt = (f"Recommend tracks for this situation: {situation}\n"
              + (f"Avoid: {avoid}\n" if avoid else "")
              + (("Choose only from these candidates and give one line of reasoning "
                  "each:\n" + "\n".join(f"- {c}" for c in cands) + "\n") if cands else "")
              + f"My preferences: {', '.join(prefs) or 'none saved'}\n"
              + f"Related memories:\n{_items_block(items)}\n{RULES}")
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
        lines.append("Ranked from your memories (a higher score is closer to the "
                     "situation and your preferences)")
        lines += [f"{n}. {c}  ({s:.2f})" for n, (s, c) in enumerate(ranked, 1)]
        lines.append("")
    else:
        lines += ["No candidate tracks, so there is nothing to rank.",
                  "Add two or more track titles and Mirror ranks them from your "
                  "memories.", ""]
    lines.append("Criteria (from your memories)")
    lines.append(f"- Preferences: {', '.join(prefs)}" if prefs
                 else "- No music preferences saved yet.")
    for it in items:
        if it["kind"] == schema.HYPOTHESIS:
            lines.append(f"- {it['statement']} ({len(it['supports'])} evidence)")
    n_obs = sum(1 for i in items if i["kind"] == schema.OBSERVATION)
    if n_obs < 3:
        lines.append(f"Only {n_obs} memories relate to music, so the ranking is weak.")
    return "local", "\n".join(lines), items


def _plan(store, index, prof, inputs, generate):
    _require(inputs, "tasks")
    tasks = _lines(inputs["tasks"])
    state = inputs.get("state") or "Normal"
    tired = state in TIRED_STATES
    items = _context(store, index, f"{state} day plan tasks {' '.join(tasks[:3])}")
    # look at stored hypotheses directly instead of relying on search scores
    rest_hyp = None
    for t in prof["tendencies"]:
        low = t["statement"].lower()
        if any(w in low for w in TIRED_WORDS) and any(w in low for w in REST_WORDS):
            rest_hyp = {"kind": schema.HYPOTHESIS, "id": t["id"],
                        "statement": t["statement"], "status": t["status"],
                        "supports": store.supporting_observations(t["id"])}
            items = items + [rest_hyp]
            break
    prompt = (f"Organize today's tasks. My state: {state}\n"
              + "\n".join(f"- {t}" for t in tasks)
              + f"\nRelated memories:\n{_items_block(items)}\n{RULES}")
    mode, text = generate(prompt)
    if mode != "local" and text:
        return mode, text, items
    lines = [f"State: {state}"]
    if tired and rest_hyp is not None:
        lines += [f"What repeats in your memories: {rest_hyp['statement']} "
                  f"({len(rest_hyp['supports'])} evidence)",
                  "So today the number of choices is kept small.", "",
                  "Today (at most 3)"]
        lines += [f"{n}. {t}" for n, t in enumerate(tasks[:3], 1)]
        if tasks[3:]:
            lines += ["", "Push to tomorrow"] + [f"- {t}" for t in tasks[3:]]
    else:
        lines += ["", "Today (in the order you gave)"]
        lines += [f"{n}. {t}" for n, t in enumerate(tasks, 1)]
    return "local", "\n".join(lines), items


def _choice(store, index, prof, inputs, generate, router):
    _require(inputs, "request", "options")
    options = _lines(inputs["options"])
    if len(options) < 2:
        raise ScenarioError("At least two options are needed.")
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
        raise ScenarioError(f"Unknown scenario: {scenario_id}")
    prof = profile_mod.build_profile(store)
    if scenario_id == "choice_help":
        mode, text, items = _choice(store, index, prof, inputs, generate, router)
    else:
        fn = {"doc_draft": _doc, "music_recommend": _music,
              "day_plan": _plan}[scenario_id]
        mode, text, items = fn(store, index, prof, inputs, generate)
    return {"scenario": scenario_id, "mode": mode, "text": text,
            "citations": _cite(items),
            "note": scenario.needs_llm if mode == "local" else ""}
