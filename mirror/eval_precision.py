"""추출 정밀도 채점 하네스 (계획서 9장, P1).

정답 JSONL 한 줄:
{"source_id": "...", "text": "...", "occurred_at": "...",
 "triples": [{"rel": "SUPPORTS", "from_kind": "Observation", "from_key": "obs",
              "to_kind": "Hypothesis", "to_key": "hyp:<statement>"}, ...]}

키 규격: obs / ent:<name> / ctx:<value> / pref:<target>:<+/-> /
crit:<statement> / hyp:<statement> / sty:<pattern>

지표: 정답·부분·누락·오류 건수를 그대로 보고 (통과 기준은 사전에 정하지 않음).
"""

from __future__ import annotations

import argparse
import json
import os

from . import extract as ex
from . import schema


def node_key(kind: str, attrs: dict) -> str:
    if kind == schema.OBSERVATION:
        return "obs"
    if kind == schema.ENTITY:
        return f"ent:{attrs.get('name', '')}"
    if kind == schema.CONTEXT:
        return f"ctx:{attrs.get('value', '')}"
    if kind == schema.PREFERENCE:
        return f"pref:{attrs.get('target', '')}:{attrs.get('direction', '+')}"
    if kind == schema.CRITERION:
        return f"crit:{attrs.get('statement', '')}"
    if kind == schema.HYPOTHESIS:
        return f"hyp:{attrs.get('statement', '')}"
    if kind == schema.STYLE:
        return f"sty:{attrs.get('pattern', '')}"
    return f"?:{kind}"


def triples_of(result: ex.ExtractionResult) -> list[tuple]:
    kinds = {"obs": schema.OBSERVATION}
    keys = {"obs": "obs"}
    for node in result.nodes:
        kinds[node.ref] = node.kind
        keys[node.ref] = node_key(node.kind, node.attrs)
    triples = []
    for edge in result.edges:
        if edge.from_ref not in keys or edge.to_ref not in keys:
            continue
        triples.append((
            edge.rel,
            kinds[edge.from_ref], keys[edge.from_ref],
            kinds[edge.to_ref], keys[edge.to_ref],
        ))
    return triples


# scoped 채점: 안정된 어노테이션 규칙이 있는 관계만 센다 (eval/GUIDELINE.md).
SCOPED_RELS = ("OBSERVED_IN", "SUPPORTS", "CONDITIONED_ON", "ALTERNATIVE_TO")
CLASSES_PATH = os.path.join(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))), "eval", "hyp_classes.json")


def load_classes(path: str = CLASSES_PATH) -> dict[str, str]:
    """가설 문구 -> 종류 이름. 같은 종류는 같은 가설로 본다."""
    with open(path, encoding="utf-8") as f:
        raw = json.load(f)
    return {stmt: name for name, stmts in raw.items() for stmt in stmts}


def _canon_key(key: str, classes: dict[str, str]) -> str:
    if key.startswith("hyp:") and key[4:] in classes:
        return "hyp:#" + classes[key[4:]]
    return key


def to_scope(triples: list[tuple], classes: dict[str, str]) -> list[tuple]:
    out = []
    for rel, fk, fkey, tk, tkey in triples:
        if rel in SCOPED_RELS:
            out.append((rel, fk, _canon_key(fkey, classes),
                        tk, _canon_key(tkey, classes)))
    return out


def gold_triples(record: dict) -> list[tuple]:
    out = []
    for t in record.get("triples", []):
        out.append((t["rel"], t["from_kind"], t["from_key"],
                    t["to_kind"], t["to_key"]))
    return out


def score_scene(gold: list[tuple], pred: list[tuple]) -> dict:
    """정답·부분·누락·오류 분류.

    부분 = 끝점 쌍 일치(관계 불일치) / 같은 관계+같은 도착점 /
    같은 관계+같은 출발점(단, 자명한 obs 출발은 제외).
    """
    gold_left = list(gold)
    pred_left = list(pred)
    exact = 0
    for t in list(pred_left):
        if t in gold_left:
            exact += 1
            gold_left.remove(t)
            pred_left.remove(t)
    partial = 0
    partial_pairs = []
    for p in list(pred_left):
        match = None
        for g in gold_left:
            same_ends = (p[1], p[2], p[3], p[4]) == (g[1], g[2], g[3], g[4])
            same_to = p[0] == g[0] and (p[3], p[4]) == (g[3], g[4])
            same_from = (
                p[0] == g[0] and (p[1], p[2]) == (g[1], g[2])
                and p[2] != "obs"
            )
            if same_ends or same_to or same_from:
                match = g
                break
        if match is not None:
            partial += 1
            partial_pairs.append({"pred": p, "gold": match})
            gold_left.remove(match)
            pred_left.remove(p)
    # 오류 유형 분류
    gold_keys = {k for t in gold for k in (t[2], t[4])}
    error_types: dict[str, int] = {}
    for p in pred_left:
        if p[2] not in gold_keys or p[4] not in gold_keys:
            err = "endpoint_not_in_gold"
        else:
            err = "wrong_relation"
        error_types[err] = error_types.get(err, 0) + 1
    return {
        "gold": len(gold), "predicted": len(pred),
        "exact": exact, "partial": partial,
        "missing": len(gold_left), "spurious": len(pred_left),
        "missing_triples": [list(t) for t in gold_left],
        "spurious_triples": [list(t) for t in pred_left],
        "partial_pairs": [{"pred": list(p["pred"]), "gold": list(p["gold"])}
                          for p in partial_pairs],
        "error_types": error_types,
    }


def score_file(path: str, extractor: ex.Extractor,
               scoped: bool = False) -> dict:
    classes = load_classes() if scoped else {}
    scenes = []
    totals = {"gold": 0, "predicted": 0, "exact": 0, "partial": 0,
              "missing": 0, "spurious": 0, "extract_failed": 0,
              "unscored_pred": 0}
    error_types: dict[str, int] = {}
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            record = json.loads(line)
            obs_in = ex.ObservationInput(
                source_id=record.get("source_id", ""),
                text=record.get("text", ""),
                occurred_at=record.get("occurred_at", "unknown"))
            try:
                result = extractor.extract(obs_in)
            except (ex.ExtractionError, ex.NotConfigured) as e:
                totals["extract_failed"] += 1
                scenes.append({"source_id": obs_in.source_id,
                               "error": str(e)})
                continue
            errors = ex.validate_extraction(result)
            if errors:
                totals["extract_failed"] += 1
                scenes.append({"source_id": obs_in.source_id,
                               "error": "; ".join(errors)})
                continue
            gold_t, pred_t = gold_triples(record), triples_of(result)
            unscored = 0
            if scoped:
                unscored = len(pred_t) - len(to_scope(pred_t, classes))
                gold_t, pred_t = to_scope(gold_t, classes), to_scope(pred_t, classes)
            scene = score_scene(gold_t, pred_t)
            scene["unscored_pred"] = unscored
            scene["source_id"] = obs_in.source_id
            for k in ("gold", "predicted", "exact", "partial",
                      "missing", "spurious", "unscored_pred"):
                totals[k] += scene[k]
            for k, v in scene["error_types"].items():
                error_types[k] = error_types.get(k, 0) + v
            scenes.append(scene)
    totals["error_types"] = error_types
    totals["scenes"] = len(scenes)
    if totals["predicted"]:
        totals["exact_precision"] = round(totals["exact"] / totals["predicted"], 4)
    else:
        totals["exact_precision"] = None
    if totals["gold"]:
        totals["exact_recall"] = round(totals["exact"] / totals["gold"], 4)
    else:
        totals["exact_recall"] = None
    return {"totals": totals, "scenes": scenes}


PROTOCOL_SETS = (("dev", "gold_observations.jsonl"),
                 ("tune", "gold_tune.jsonl"),
                 ("tune2", "gold_tune2.jsonl"),
                 ("heldout2", "gold_heldout2.jsonl"),
                 ("heldout3", "gold_heldout3.jsonl"))


def protocol_report(extractor: ex.Extractor, eval_dir: str,
                    sets: tuple = PROTOCOL_SETS) -> list[dict]:
    """세트별 strict/scoped 숫자를 그대로 모은다."""
    rows = []
    for name, fname in sets:
        path = os.path.join(eval_dir, fname)
        if not os.path.exists(path):
            continue  # 개인 샘플 세트는 레포에 포함되지 않는다
        for mode in ("strict", "scoped"):
            t = score_file(path, extractor, scoped=(mode == "scoped"))["totals"]
            rows.append({"set": name, "mode": mode, **{
                k: t[k] for k in ("scenes", "gold", "predicted", "exact",
                                  "partial", "missing", "spurious",
                                  "unscored_pred", "exact_precision",
                                  "exact_recall")}})
    return rows


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Mirror extraction precision")
    parser.add_argument("gold", nargs="?", help="gold triples JSONL path")
    parser.add_argument("--protocol", action="store_true",
                        help="dev/tune/heldout 세트를 strict·scoped로 채점")
    parser.add_argument("--scoped", action="store_true")
    parser.add_argument("--extractor", default="mock",
                        choices=["mock", "spark", "Muse", "ollama"])
    parser.add_argument("--model", default="")
    args = parser.parse_args(argv)
    kwargs = {"model": args.model} if args.model else {}
    extractor = ex.get_extractor(args.extractor, **kwargs)
    if args.protocol:
        eval_dir = os.path.dirname(CLASSES_PATH)
        for r in protocol_report(extractor, eval_dir):
            print(json.dumps(r, ensure_ascii=False))
        return 0
    if not args.gold:
        parser.error("gold path or --protocol required")
    report = score_file(args.gold, extractor, scoped=args.scoped)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
