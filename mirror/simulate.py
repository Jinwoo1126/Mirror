"""관점 시뮬레이터 P4 (계획서 7장, MiroFish의 소형 변형).

결정이 걸린 요청에서만 켠다. 경쟁 가설마다 "이 가설을 믿는 예측자"를 만들어
선택을 예측하고, 조건을 바꿔 다시 묻는다(what-if).
비용 상한: 요청당 예측자 5개, 라운드 2회.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from . import llm as llm_mod
from . import retrieve as retrieve_mod
from . import schema
from .store import MirrorStore

MAX_PREDICTORS = 5
MAX_ROUNDS = 2


@dataclass
class PredictorResult:
    hyp_id: str
    statement: str
    choice: str | None
    why: str
    evidence_ids: list[str] = field(default_factory=list)


@dataclass
class SimulationResult:
    request: str
    options: list[str]
    conditions: list[dict]
    predictors: list[PredictorResult]
    votes: dict
    abstains: int
    agree: bool
    majority: str | None
    what_if: list[dict] = field(default_factory=list)
    predictors_used: int = 0
    rounds_used: int = 0
    note: str = ""


def _overlap_score(text: str, corpus: str) -> int:
    tokens = retrieve_mod.tokenize(text)
    return sum(1 for t in tokens if t in corpus)


class MockPredictor:
    """결정적 예측자: 가설+근거와 선택지의 관련도로 투표.

    scorer가 있으면 의미 점수로 순위화하고 0.70 미만은 기권한다.
    없으면 키워드 겹침으로 투표한다.
    """

    name = "mock"

    TIEBREAK_MARGIN = 0.03

    def __init__(self, scorer=None):
        self.scorer = scorer

    def predict(self, hyp: schema.Hypothesis, support_texts: list[str],
                request: str, options: list[str],
                conditions: list[dict]) -> tuple[str | None, str]:
        segments = [hyp.statement, *support_texts,
                    *[str(c.get("value", "")) for c in conditions]]
        corpus = " ".join(segments)
        scored = sorted(((opt, _overlap_score(opt, corpus)) for opt in options),
                        key=lambda t: t[1], reverse=True)
        if not scored or scored[0][1] <= 0:
            return None, "no overlap between view and options"
        if len(scored) == 1 or scored[0][1] > scored[1][1]:
            detail = ",".join(f"{o}={s}" for o, s in scored)
            return scored[0][0], f"overlap scores {detail}"
        if self.scorer is None:
            return None, "lexical tie, no scorer"
        tied = [o for o, s in scored if s == scored[0][1]]
        sem = sorted(
            ((o, max((self.scorer(o, seg) for seg in segments if seg.strip()),
                     default=0.0)) for o in tied),
            key=lambda t: t[1], reverse=True)
        if len(sem) > 1 and sem[0][1] - sem[1][1] < self.TIEBREAK_MARGIN:
            return None, "semantic tie"
        detail = ",".join(f"{o}={round(s, 4)}" for o, s in sem)
        return sem[0][0], f"lexical tie broken semantically: {detail}"


PREDICT_SYSTEM = """You are a predictor who believes ONE hypothesis about a person.
Given the hypothesis, its supporting observations, and a choice, predict which
option the person would pick. Output JSON only:
{"choice": "<exact option text or null>", "why": "<one sentence>"}.
Use null when the view says nothing about the options. Never invent new options."""


class LlmPredictor:
    """LLM 예측자. 실패한 가설은 Mock으로 대체한다."""

    name = "llm"

    def __init__(self, backend: str = "spark", model: str | None = None,
                 fallback: MockPredictor | None = None, **llm_kwargs):
        self.backend = backend
        self.model = model
        self.fallback = fallback or MockPredictor()
        self.llm_kwargs = llm_kwargs

    def _call(self, user: str) -> dict:
        if self.backend == "spark":
            out = llm_mod.run_muse_exec(
                PREDICT_SYSTEM + "\n\n" + user,
                model=self.model or "muse-spark-1.3", **self.llm_kwargs)
            return llm_mod.parse_json_output(out)
        if self.backend == "Muse":
            return llm_mod.call_claude_json(
                PREDICT_SYSTEM, user, model=self.model or "claude-haiku-4-5")
        if self.backend == "ollama":
            return llm_mod.call_ollama_json(
                PREDICT_SYSTEM, user, model=self.model or "llama3.1")
        raise llm_mod.LlmError(f"unknown backend: {self.backend}")

    def predict(self, hyp, support_texts, request, options, conditions):
        conds = ", ".join(f"{c.get('kind')}:{c.get('value')}"
                          for c in conditions) or "(none)"
        supports = "\n".join(f"- {t[:300]}" for t in support_texts[:5])
        user = (f"hypothesis: {hyp.statement}\n"
                f"supporting observations:\n{supports or '(none)'}\n"
                f"request: {request}\nconditions: {conds}\n"
                f"options: {options}\nPredict as JSON.")
        try:
            data = self._call(user)
            choice = data.get("choice")
            if choice is not None and choice not in options:
                raise ValueError(f"choice not in options: {choice}")
            return choice, str(data.get("why", ""))
        except (llm_mod.LlmError, ValueError, AttributeError) as e:
            choice, why = self.fallback.predict(
                hyp, support_texts, request, options, conditions)
            return choice, f"{why} (llm fallback: {e})"


def pick_hypotheses(store: MirrorStore, request: str, options: list[str],
                    as_of: str, max_n: int = MAX_PREDICTORS
                    ) -> list[schema.Hypothesis]:
    """관련 활성·보류 가설 중 ALTERNATIVE_TO 경쟁군 최대 max_n개."""
    pool: list[schema.Hypothesis] = []
    for status in (schema.STATUS_ACTIVE, schema.STATUS_SUSPENDED):
        for hid in store.hypothesis_ids_by_status([status], as_of):
            hyp = store.get_hypothesis(hid)
            if hyp is not None:
                pool.append(hyp)
    if not pool:
        return []
    corpus = request + " " + " ".join(options)
    pool.sort(key=lambda h: _overlap_score(h.statement, corpus), reverse=True)
    picked = [pool[0]]
    for alt_id in store.alternatives(pool[0].id):
        if len(picked) >= max_n:
            break
        alt = next((h for h in pool if h.id == alt_id), None)
        if alt is not None and alt not in picked:
            picked.append(alt)
    for hyp in pool[1:]:
        if len(picked) >= max_n:
            break
        if hyp not in picked:
            picked.append(hyp)
    return picked


def _support_texts(store: MirrorStore, hyp_id: str, as_of: str) -> list[str]:
    texts = []
    for obs_id in store.supporting_observations(hyp_id, as_of=as_of):
        obs = store.get_observation(obs_id)
        if obs is not None and obs.text:
            texts.append(obs.text)
    return texts


def simulate(store: MirrorStore, request: str, options: list[str],
             conditions: list[dict] | None = None,
             what_ifs: list[dict] | None = None,
             as_of: str | None = None,
             predictor=None,
             max_predictors: int = MAX_PREDICTORS,
             max_rounds: int = MAX_ROUNDS) -> SimulationResult:
    """관점 시뮬레이션 1회. 예측 일치는 조용한 반영, 불일치는 선택지 병렬."""
    as_of = as_of or schema.utcnow_iso()
    conditions = conditions or []
    predictor = predictor or MockPredictor()
    max_predictors = min(max_predictors, MAX_PREDICTORS)
    max_rounds = min(max_rounds, MAX_ROUNDS)

    hyps = pick_hypotheses(store, request, options, as_of, max_predictors)
    if len(hyps) < 2:
        note = ("insufficient_views" if not hyps else "single_view")
    else:
        note = ""

    def run_round(conds: list[dict]) -> list[PredictorResult]:
        out = []
        for hyp in hyps:
            supports = store.supporting_observations(hyp.id, as_of=as_of)
            choice, why = predictor.predict(
                hyp, _support_texts(store, hyp.id, as_of),
                request, options, conds)
            out.append(PredictorResult(hyp_id=hyp.id, statement=hyp.statement,
                                       choice=choice, why=why,
                                       evidence_ids=supports))
        return out

    predictors = run_round(conditions)
    votes: dict[str, int] = {}
    for p in predictors:
        if p.choice is not None:
            votes[p.choice] = votes.get(p.choice, 0) + 1
    abstains = sum(1 for p in predictors if p.choice is None)
    majority = max(votes, key=lambda k: votes[k]) if votes else None
    agree = len(votes) == 1 and abstains == 0 and bool(votes)

    if what_ifs is None:
        what_ifs = ([{"label": "조건 완화", "conditions": []}]
                    if conditions else [])
    what_if_runs = []
    for spec in what_ifs[: max(0, max_rounds - 1)]:
        wconds = spec.get("conditions", [])
        wres = run_round(wconds)
        wvotes: dict[str, int] = {}
        for p in wres:
            if p.choice is not None:
                wvotes[p.choice] = wvotes.get(p.choice, 0) + 1
        what_if_runs.append({
            "label": spec.get("label", "what-if"), "conditions": wconds,
            "votes": wvotes,
            "majority": max(wvotes, key=lambda k: wvotes[k]) if wvotes else None,
            "predictors": [schema.to_jsonable(p) for p in wres],
        })

    return SimulationResult(
        request=request, options=list(options), conditions=conditions,
        predictors=predictors, votes=votes, abstains=abstains,
        agree=agree, majority=majority, what_if=what_if_runs,
        predictors_used=len(predictors), rounds_used=1 + len(what_if_runs),
        note=note)


def record_outcomes(store: MirrorStore, result: SimulationResult,
                    episode_id: str, actual: str) -> int:
    """실제 선택 공개 후 예측자별 적중 기록. 기록 건수 반환."""
    n = 0
    for p in result.predictors:
        if p.choice is not None:
            store.log_prediction(p.hyp_id, episode_id, p.choice, actual)
            n += 1
    return n
