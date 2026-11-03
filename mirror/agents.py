"""비교 실험용 에이전트 P3: A(강한 기억) vs M1(그래프) vs M2(그래프+시뮬레이터).

모든 에이전트는 예측 시점에 공개된 과거만 본다. 리플레이 하네스가 에피소드를
시간순으로 하나씩 공개하므로, 저장소 자체가 과거만을 담는다.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol

from . import retrieve as retrieve_mod
from . import schema
from . import simulate as simulate_mod
from .store import MirrorStore


@dataclass
class AgentPrediction:
    choice: str | None  # None = 보류(abstain)
    rationale: str
    evidence_ids: list[str] = field(default_factory=list)
    hyp_votes: dict = field(default_factory=dict)


class Agent(Protocol):
    name: str

    def predict(self, episode, store: MirrorStore, as_of: str) -> AgentPrediction:
        ...


def _overlap(text: str, corpus: str) -> int:
    return sum(1 for t in retrieve_mod.tokenize(text) if t in corpus)


class MemoryAgent:
    """A: 강한 기억. 과거 선택 기록으로 투표, 근거 없으면 보류."""

    name = "A"

    def __init__(self, index=None):
        self.index = index

    def predict(self, episode, store, as_of):
        if self.index is not None:
            hits = self.index.search_obs(
                episode.text + " " + " ".join(episode.options),
                store, as_of=as_of, top_k=50)
            past = [(h["obs"], h["score"]) for h in hits]
        else:
            keywords = retrieve_mod.tokenize(
                episode.text + " " + " ".join(episode.options))
            past = [(o, 1.0) for o in store.search_observations(
                keywords, as_of=as_of, limit=50)]
        if not past:
            return AgentPrediction(None, "no past records", [], {})
        scored = []
        for opt in episode.options:
            votes = [(o.id, w if _overlap(opt, o.text or "") > 0 else 0.0)
                     for o, w in past]
            total = round(sum(s for _, s in votes), 4)
            scored.append((opt, total, [i for i, s in votes if s > 0][:3]))
        best = max(scored, key=lambda t: t[1])
        if best[1] <= 0:
            return AgentPrediction(None, "no overlapping memory", [], {})
        return AgentPrediction(best[0], f"memory overlap {best[1]}",
                               best[2], {})


class GraphAgent:
    """M1: 그래프. 활성·후보 가설이 예측에 투표 (internal_only도 예측 허용)."""

    name = "M1"

    def __init__(self, index=None):
        self.index = index

    def _vote(self, segments: list[str], options: list[str]) -> str | None:
        corpus = " ".join(segments)
        scored = sorted(((o, _overlap(o, corpus)) for o in options),
                        key=lambda t: t[1], reverse=True)
        if not scored or scored[0][1] <= 0:
            return None
        if len(scored) == 1 or scored[0][1] > scored[1][1]:
            return scored[0][0]
        # 동점일 때만 의미 점수로 해소
        if self.index is None:
            return None
        tied = [o for o, s in scored if s == scored[0][1]]
        sem = sorted(((o, max((self.index.score(o, seg) for seg in segments),
                              default=0.0)) for o in tied),
                     key=lambda t: t[1], reverse=True)
        if len(sem) > 1 and sem[0][1] - sem[1][1] < 0.03:
            return None
        return sem[0][0]

    def predict(self, episode, store, as_of):
        rows = store._rows(
            "MATCH (h:Hypothesis) WHERE h.status IN ['active', 'candidate'] "
            "AND h.recorded_at <= $as_of RETURN h.id",
            {"as_of": as_of},
        )
        hyp_votes: dict[str, str] = {}
        evidence: dict[str, list[str]] = {}
        for (hid,) in rows:
            hyp = store.get_hypothesis(hid)
            supports = store.supporting_observations(hid, as_of=as_of)
            texts = []
            for oid in supports:
                obs = store.get_observation(oid)
                if obs is not None and obs.text:
                    texts.append(obs.text)
            segments = [hyp.statement, *texts]
            corpus = " ".join(segments)
            if _overlap(episode.text, corpus) <= 0:
                continue
            voted = self._vote(segments, episode.options)
            if voted is not None:
                hyp_votes[hid] = voted
                evidence[hid] = supports
        if not hyp_votes:
            return AgentPrediction(None, "no hypothesis fires", [], {})
        tally: dict[str, int] = {}
        for choice in hyp_votes.values():
            tally[choice] = tally.get(choice, 0) + 1
        top = sorted(tally.items(), key=lambda t: t[1], reverse=True)
        if len(top) > 1 and top[0][1] == top[1][1]:
            return AgentPrediction(None, f"hypothesis tie {tally}",
                                   [], hyp_votes)
        ev = sorted({o for h, c in hyp_votes.items() if c == top[0][0]
                     for o in evidence[h]})[:5]
        return AgentPrediction(top[0][0], f"hypothesis votes {tally}", ev,
                               hyp_votes)


class SimAgent:
    """M2: 그래프+시뮬레이터. 경쟁 관점의 다수결, 동점이면 보류."""

    name = "M2"

    def __init__(self, predictor=None, index=None):
        if predictor is None and index is not None:
            predictor = simulate_mod.MockPredictor(scorer=index.score)
        self.predictor = predictor or simulate_mod.MockPredictor()

    def predict(self, episode, store, as_of):
        sim = simulate_mod.simulate(
            store, episode.text, episode.options, episode.conditions,
            as_of=as_of, predictor=self.predictor)
        hyp_votes = {p.hyp_id: p.choice for p in sim.predictors
                     if p.choice is not None}
        if not sim.votes:
            return AgentPrediction(None, f"simulator abstains ({sim.note})",
                                   [], {})
        top = sorted(sim.votes.items(), key=lambda t: t[1], reverse=True)
        if len(top) > 1 and top[0][1] == top[1][1]:
            return AgentPrediction(None, f"simulator tie {sim.votes}",
                                   [], hyp_votes)
        ev = sorted({o for p in sim.predictors if p.choice == top[0][0]
                     for o in p.evidence_ids})[:5]
        return AgentPrediction(
            top[0][0],
            f"simulator votes {sim.votes} agree={sim.agree} "
            f"what_if={[w['majority'] for w in sim.what_if]}",
            ev, hyp_votes)


def get_agent(name: str, **kwargs) -> Agent:
    if name == "A":
        return MemoryAgent(**kwargs)
    if name == "M1":
        return GraphAgent(**kwargs)
    if name == "M2":
        return SimAgent(**kwargs)
    raise ValueError(f"unknown agent: {name}")
