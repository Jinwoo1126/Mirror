"""Personal scenarios: document drafts, music picks, day plans, choice help.

A scenario is an input form, context pulled from the person's memories, and
generation instructions. With an LLM it generates from those instructions and
context. Without one it returns only what the memories can support (an outline,
a ranking of the candidates the person supplied, a rule-based plan). It never
invents track titles or body text. The memories it used come back as ids.
"""

from __future__ import annotations

import re
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
    triggers: list[str] = field(default_factory=list)  # regexes: when a hook should fire
    topic: str = ""               # what to look up in memory for this situation
    instruction: str = ""         # generation instructions (lens scenarios)
    local_hint: str = ""          # what to tell the person when nothing relevant is known
    lens: bool = False            # True: generic "what applies here" scenario


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
        "a style guide and the memories you can draw on.",
        triggers=[r"write (a|an|the) (blog|post|memo|article|essay|report|email|document|summary)", r"blog post", r"\bmemo\b", r"first draft", r"초안"]),
    Scenario(
        "music_recommend", "Pick music",
        "Choose tracks that fit the moment, based on what Mirror knows about you.",
        [Field("situation", "Situation", "text", "e.g. Walking home after a late shift",
               required=True),
         Field("candidates", "Candidate tracks (optional)", "textarea",
               "One per line. If you add some, Mirror ranks them for you"),
         Field("avoid", "Anything to avoid", "text")],
        "Finding new tracks needs an LLM connection. With candidates, they are ranked "
        "from your memories. Without, you only get the criteria.",
        triggers=[r"playlist", r"\bmusic\b", r"\btracks?\b", r"\bsongs?\b", r"음악", r"선곡"]),
    Scenario(
        "day_plan", "Plan my day",
        "Sort today's tasks to fit your state and what Mirror has learned.",
        [Field("state", "How do you feel", "select", options=["Normal", "Tired", "Fresh"]),
         Field("tasks", "Tasks", "textarea", "One per line", required=True)],
        "Planned by rules. With an LLM connected, it also explains the time split.",
        triggers=[r"plan my day", r"to-?do", r"today'?s tasks", r"할 일"]),
    Scenario(
        "choice_help", "Help me choose",
        "Weigh menus, places or movies against your own history.",
        [Field("request", "What are you choosing", "text", "e.g. Tonight's dinner order",
               required=True),
         Field("options", "Options", "textarea", "Two or more, one per line",
               required=True)],
        "Shows predictions from different viewpoints side by side. You decide.",
        triggers=[r"which (one|option)", r"choose between", r"\bor\b.*\?"]),
)}


def _lens(id, name, summary, fields, triggers, topic, instruction, hint):
    return Scenario(
        id, name, summary, fields,
        "Writing the guidance needs an LLM connection. For now you get the patterns "
        "Mirror found that matter here.",
        triggers=triggers, topic=topic, instruction=instruction, local_hint=hint, lens=True)


_F = Field
for _s in (
    _lens("code_review", "Review code my way",
          "A review checklist ordered by what you actually care about.",
          [_F("subject", "What are you reviewing", "text",
              "e.g. A pull request that refactors the billing module", required=True),
           _F("notes", "Anything specific", "textarea", "Optional")],
          [r"code review", r"pull request", r"\bPR\b", r"\bdiff\b", r"refactor", r"리뷰"],
          "code review feedback strictness tests naming refactoring",
          "Write a short review checklist for this change, ordered by what this person "
          "tends to care about. Base their priorities only on the patterns below.",
          "Tell Mirror your review habits, for example: I block pull requests without tests."),
    _lens("writing_tone", "Match my tone",
          "Rewrite or draft a message the way you would write it.",
          [_F("message", "Your draft or what to say", "textarea",
              "Paste a draft or describe the message", required=True),
           _F("audience", "Who is it for", "select",
              options=["Colleague", "Manager", "Friend", "Customer"])],
          [r"\bemail\b", r"\breply\b", r"\bdraft\b", r"rewrite", r"\btone\b", r"메일", r"답장"],
          "tone writing email message formal casual short direct",
          "Rewrite the draft in this person's tone for the audience. Follow the tone "
          "patterns below and keep the meaning unchanged.",
          "Tell Mirror how you like to write, for example: I keep emails to three sentences."),
    _lens("decision_recap", "Recall similar decisions",
          "What you usually choose in situations like this one.",
          [_F("decision", "The decision in front of you", "text",
              "e.g. Whether to take the new project", required=True)],
          [r"should i", r"\bdecide\b", r"choose between", r"\bpick\b", r"\bvs\b", r"고민"],
          "decision choice pick usually default avoid prefer",
          "Describe what this person usually chooses in similar situations and why, "
          "using only the patterns below. Do not make the decision for them.",
          "Tell Mirror how you decide, for example: I pick the familiar option when tired."),
    _lens("deadline_pressure", "Under pressure",
          "What you cut and what you protect when time is short.",
          [_F("situation", "What is happening", "textarea", required=True),
           _F("deadline", "Deadline", "text", "e.g. Friday noon")],
          [r"deadline", r"behind schedule", r"crunch", r"overdue", r"tight schedule", r"빠듯", r"마감"],
          "deadline pressure schedule tight cut skip prioritize stress",
          "Suggest what to cut, keep and delay, based on how this person has handled "
          "tight schedules before. Use only the patterns below.",
          "Tell Mirror what you do when time is short."),
    _lens("learning_style", "Explain it my way",
          "How to explain something so it sticks for you.",
          [_F("topic", "What do you want to learn", "text", required=True),
           _F("level", "Your level", "select",
              options=["Beginner", "Some experience", "Expert"])],
          [r"\bexplain\b", r"teach me", r"how does", r"tutorial", r"walk me through", r"설명"],
          "learning explanation examples depth order step",
          "Outline how to explain the topic to this person: the order, the depth and "
          "the kind of examples, based only on the patterns below.",
          "Tell Mirror how you learn best, for example: I want the example before the rule."),
    _lens("ai_instructions", "Brief an AI for me",
          "Instructions that make an AI answer the way you like.",
          [_F("task", "What will the AI do for you", "text", required=True)],
          [r"system prompt", r"prompt for", r"instructions for", r"ask the ai", r"custom instructions"],
          "AI assistant answers length questions style prefer concise",
          "Write instructions for an AI assistant that make its answers fit this person, "
          "using only the patterns below as evidence of their preferences.",
          "Tell Mirror what annoys you in AI answers, for example: too long, too many questions."),
    _lens("collaboration", "Work with someone",
          "How you tend to handle a teammate, manager or stakeholder.",
          [_F("who", "Who", "text", "e.g. My manager", required=True),
           _F("situation", "What is happening", "textarea", required=True)],
          [r"my manager", r"teammate", r"colleague", r"stakeholder", r"\b1:1\b", r"feedback from", r"팀장", r"동료"],
          "teammate manager feedback communication conflict meeting",
          "Suggest how to approach this situation based on how this person has handled "
          "similar ones. Describe behavior only and never label the person or the other party.",
          "Tell Mirror how you handle feedback and disagreement."),
    _lens("shopping", "Buy something",
          "How you usually decide on a purchase.",
          [_F("item", "What are you buying", "text", required=True),
           _F("budget", "Budget", "text")],
          [r"\bbuy\b", r"purchase", r"worth it", r"\bbudget\b", r"\bprice\b", r"구매", r"예산"],
          "buy purchase price budget compare brand spend",
          "Summarize how this person usually decides on purchases and what to weigh "
          "here, using only the patterns below.",
          "Tell Mirror how you buy things, for example: I wait a week before big purchases."),
    _lens("energy_routine", "Fit my energy",
          "Schedule things around how much energy you have.",
          [_F("task", "What do you need to fit in", "textarea", required=True),
           _F("energy", "Your energy", "select", options=["Low", "Normal", "High"])],
          [r"\btired\b", r"exhausted", r"\benergy\b", r"\bsleep\b", r"workout", r"피곤", r"지쳐"],
          "tired energy rest sleep workout routine slow",
          "Suggest how to arrange this around the person's energy, using only the "
          "patterns below about how they act when tired or fresh.",
          "Tell Mirror what you do when you are tired."),
    _lens("travel_plan", "Plan a trip",
          "A trip shaped by how you actually like to travel.",
          [_F("destination", "Where", "text", required=True),
           _F("days", "How many days", "text")],
          [r"\btrip\b", r"\btravel\b", r"itinerary", r"\bflight\b", r"\bhotel\b", r"여행"],
          "travel trip places walk food pace budget new",
          "Shape a trip plan around this person's habits with places, food and pace, "
          "using only the patterns below.",
          "Tell Mirror how you like to travel."),
):
    SCENARIOS[_s.id] = _s



def list_scenarios() -> list[dict]:
    return [schema.to_jsonable(s) for s in SCENARIOS.values()]


def match_scenarios(text: str, limit: int = 3) -> list[dict]:
    """Which scenarios a piece of text triggers, best first. This is what a hook calls."""
    hits = []
    for sc in SCENARIOS.values():
        n = sum(1 for t in sc.triggers if re.search(t, text or "", re.IGNORECASE))
        if n:
            hits.append({"id": sc.id, "name": sc.name, "score": n, "topic": sc.topic})
    hits.sort(key=lambda h: -h["score"])
    return hits[:limit]


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


def _lens_run(store, index, prof, inputs, generate, sc):
    _require(inputs, *[f.key for f in sc.fields if f.required])
    given = [(f.label, str(inputs.get(f.key, "")).strip()) for f in sc.fields
             if str(inputs.get(f.key, "")).strip()]
    items = _context(store, index, sc.topic + " " + " ".join(v for _, v in given), budget=8)
    patterns = [i for i in items if i["kind"] == schema.HYPOTHESIS]
    prompt = (sc.instruction + "\n" + "\n".join(f"{k}: {v}" for k, v in given)
              + f"\nPatterns about this person:\n{_items_block(items)}\n{RULES}\n"
              "If none of the patterns are relevant, say so briefly instead of guessing.")
    mode, text = generate(prompt)
    if mode != "local" and text:
        return mode, text, items
    lines = [sc.name, ""] + [f"{k}: {v}" for k, v in given] + [""]
    if patterns:
        lines.append("Patterns that matter here (from what Mirror knows about you)")
        lines += [f"- {p['statement']} ({len(p['supports'])} evidence)" for p in patterns]
    else:
        lines += ["Mirror has no patterns relevant to this yet.", sc.local_hint]
    return "local", "\n".join(lines), items


def run_scenario(store: MirrorStore, router, index, scenario_id: str,
                 inputs: dict, generate: Generate) -> dict:
    scenario = SCENARIOS.get(scenario_id)
    if scenario is None:
        raise ScenarioError(f"Unknown scenario: {scenario_id}")
    prof = profile_mod.build_profile(store)
    if scenario.lens:
        mode, text, items = _lens_run(store, index, prof, inputs, generate, scenario)
    elif scenario_id == "choice_help":
        mode, text, items = _choice(store, index, prof, inputs, generate, router)
    else:
        fn = {"doc_draft": _doc, "music_recommend": _music,
              "day_plan": _plan}[scenario_id]
        mode, text, items = fn(store, index, prof, inputs, generate)
    return {"scenario": scenario_id, "mode": mode, "text": text,
            "citations": _cite(items),
            "note": scenario.needs_llm if mode == "local" else ""}
