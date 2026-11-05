"""Postgres (Supabase) implementation of the Mirror store.

Same public contract as the embedded Kuzu `MirrorStore`, scoped to one user.

Tenancy is enforced twice. Every query filters on `user_id`, and every statement runs
inside a transaction that first switches to the `authenticated` role and sets the
user's id as the JWT subject, so the row level security policies in
supabase/migrations apply even if a query here ever forgets its filter. This is the
same thing PostgREST does for the Supabase Data API.
"""

from __future__ import annotations

import uuid
from contextlib import contextmanager

from . import schema
from . import tokens as tokens_mod
from .schema import (
    CONTEXT,
    CORRECTION,
    CRITERION,
    ENTITY,
    HYPOTHESIS,
    OBSERVATION,
    PREFERENCE,
    RESPONSE,
    STYLE,
    Edge,
    SchemaError,
)
from .store import MirrorStore

# node kind -> (table, columns in the order get_node reports them)
NODES: dict[str, tuple[str, list[str]]] = {
    OBSERVATION: ("observations", ["id", "source_id", "text", "occurred_at", "raw_ref",
                                   "visibility", "recorded_at"]),
    ENTITY: ("entities", ["id", "kind", "name", "aliases", "recorded_at", "description"]),
    CONTEXT: ("contexts", ["id", "kind", "value", "valid_from", "valid_to", "recorded_at"]),
    PREFERENCE: ("preferences", ["id", "target", "direction", "strength_evidence",
                                 "valid_from", "valid_to", "recorded_at"]),
    CRITERION: ("criteria", ["id", "statement", "applies_to", "recorded_at"]),
    HYPOTHESIS: ("hypotheses", ["id", "conditions", "statement", "status", "use_policy",
                                "domain", "valid_from", "valid_to", "recorded_at",
                                "status_changed_at"]),
    STYLE: ("styles", ["id", "pattern", "example_refs", "intent", "recorded_at"]),
    CORRECTION: ("corrections", ["id", "kind", "target", "text", "recorded_at"]),
    RESPONSE: ("responses", ["id", "text", "created_at"]),
}


def _table(kind: str) -> str:
    if kind not in NODES:
        raise SchemaError(f"unknown node type: {kind}")
    return NODES[kind][0]


class PostgresStore(MirrorStore):
    """One user's knowledge graph in Postgres. `dsn` connects as a role that may
    `SET ROLE authenticated` (the Supabase `postgres` role can)."""

    def __init__(self, dsn: str, user_id: str):
        import psycopg

        self.path = dsn
        self.uid = uuid.UUID(str(user_id))
        self._conn = psycopg.connect(dsn, autocommit=True)

    def close(self) -> None:
        if self._conn is not None:
            self._conn.close()
        self._conn = None  # type: ignore[assignment]

    # -- plumbing ----------------------------------------------------------

    @contextmanager
    def _tx(self):
        with self._conn.transaction():
            self._conn.execute("set local role authenticated")
            self._conn.execute(
                "select set_config('request.jwt.claim.sub', %s, true)", (str(self.uid),))
            yield self._conn

    def _q(self, sql: str, params: tuple | list = ()) -> list[tuple]:
        with self._tx() as c:
            return c.execute(sql, params).fetchall()

    def _q1(self, sql: str, params: tuple | list = ()) -> tuple | None:
        rows = self._q(sql, params)
        return rows[0] if rows else None

    def _x(self, sql: str, params: tuple | list = ()) -> int:
        with self._tx() as c:
            return c.execute(sql, params).rowcount

    def _rows(self, query, params=None):  # pragma: no cover - guard
        raise NotImplementedError("PostgresStore has no Cypher; use a store method")

    _one = _rows

    def _insert(self, kind: str, values: dict) -> None:
        table, _ = NODES[kind]
        cols = ["user_id", *values]
        marks = ", ".join(["%s"] * len(cols))
        self._x(f"insert into public.{table} ({', '.join(cols)}) values ({marks})",
                [self.uid, *values.values()])

    EXPORT_TABLES = ["observations", "entities", "contexts", "preferences", "criteria",
                     "hypotheses", "styles", "corrections", "edges",
                     "blocked_understandings", "predictions", "intervention_logs"]

    def export_all(self) -> dict:
        """Everything stored about this user, as plain data (right of access/portability).
        Leaves out internals: token hashes, job queue, row ordering."""
        out: dict = {"exported_at": schema.utcnow_iso(), "user_id": str(self.uid)}
        with self._tx() as c:
            for table in self.EXPORT_TABLES:
                cur = c.execute(f"select * from public.{table} where user_id=%s order by seq",
                                (self.uid,))
                cols = [d.name for d in cur.description]
                keep = [i for i, name in enumerate(cols) if name not in ("user_id", "seq")]
                out[table] = [{cols[i]: row[i] for i in keep} for row in cur.fetchall()]
        return out

    def delete_all_data(self) -> None:
        """Erase everything this user has stored (account deletion / right to erasure)."""
        tables = ["edges", "observations", "entities", "contexts", "preferences",
                  "criteria", "hypotheses", "styles", "corrections", "responses",
                  "meta", "blocked_understandings", "predictions", "intervention_logs",
                  "jobs", "api_tokens"]
        with self._tx() as c:
            for table in tables:
                c.execute(f"delete from public.{table} where user_id=%s", (self.uid,))

    # -- background jobs ----------------------------------------------------

    MAX_QUEUED = 20

    def enqueue_job(self, kind: str, payload: dict) -> str:
        """Queue work for the background worker. Refuses when too much is already waiting."""
        import json

        waiting = self._q1("select count(*) from public.jobs where user_id=%s and "
                           "status in ('queued','running')", (self.uid,))[0]
        if waiting >= self.MAX_QUEUED:
            raise ValueError("too many memories are still being processed; try again shortly")
        jid = schema.new_id("job")
        now = schema.utcnow_iso()
        self._x("insert into public.jobs (user_id, id, kind, payload, run_after, created_at) "
                "values (%s,%s,%s,%s,%s,%s)",
                (self.uid, jid, kind, json.dumps(payload, ensure_ascii=False), now, now))
        return jid

    def get_job(self, job_id: str) -> dict | None:
        import json

        row = self._q1("select id, kind, status, attempts, created_at, finished_at, result, "
                       "error from public.jobs where user_id=%s and id=%s",
                       (self.uid, job_id))
        if row is None:
            return None
        return {"id": row[0], "kind": row[1], "status": row[2], "attempts": row[3],
                "created_at": row[4], "finished_at": row[5],
                "result": json.loads(row[6]) if row[6] else None, "error": row[7]}

    # -- personal API tokens ------------------------------------------------

    MAX_TOKENS = 10

    def create_token(self, name: str) -> dict:
        """Issue a token. The raw value is returned once and never stored."""
        name = " ".join((name or "").split())[:60]
        if not name:
            raise ValueError("a token needs a name")
        if len(self.list_tokens()) >= self.MAX_TOKENS:
            raise ValueError(f"at most {self.MAX_TOKENS} active tokens; revoke one first")
        raw = tokens_mod.generate()
        tid = schema.new_id("tok")
        self._x("insert into public.api_tokens (user_id, id, name, prefix, token_hash, "
                "created_at) values (%s,%s,%s,%s,%s,%s)",
                (self.uid, tid, name, tokens_mod.prefix(raw), tokens_mod.digest(raw),
                 schema.utcnow_iso()))
        return {"id": tid, "name": name, "token": raw, "prefix": tokens_mod.prefix(raw)}

    def list_tokens(self) -> list[dict]:
        """Active tokens, without their secrets."""
        return [{"id": r[0], "name": r[1], "prefix": r[2], "created_at": r[3],
                 "last_used_at": r[4]} for r in self._q(
            "select id, name, prefix, created_at, last_used_at from public.api_tokens "
            "where user_id=%s and revoked_at is null order by created_at, id",
            (self.uid,))]

    def revoke_token(self, token_id: str) -> bool:
        return self._x("update public.api_tokens set revoked_at=%s where user_id=%s "
                       "and id=%s and revoked_at is null",
                       (schema.utcnow_iso(), self.uid, token_id)) == 1

    # -- revision ----------------------------------------------------------

    def get_revision(self) -> int:
        row = self._q1("select value from public.meta where user_id=%s and key='revision'",
                       (self.uid,))
        return int(row[0]) if row else 0

    def bump_revision(self) -> int:
        rev = self.get_revision() + 1
        self._x("insert into public.meta (user_id, key, value) values (%s, 'revision', %s) "
                "on conflict (user_id, key) do update set value = excluded.value",
                (self.uid, str(rev)))
        return rev

    # -- Observation -------------------------------------------------------

    def add_observation(self, obs: schema.Observation) -> bool:
        return self._x(
            "insert into public.observations (user_id, id, source_id, text, occurred_at, "
            "raw_ref, visibility, recorded_at) values (%s,%s,%s,%s,%s,%s,%s,%s) "
            "on conflict (user_id, source_id) do nothing",
            (self.uid, obs.id, obs.source_id, obs.text, obs.occurred_at, obs.raw_ref,
             obs.visibility, obs.recorded_at)) == 1

    _OBS_COLS = "id, source_id, text, occurred_at, raw_ref, visibility, recorded_at"

    def get_observation(self, obs_id: str) -> schema.Observation | None:
        row = self._q1(f"select {self._OBS_COLS} from public.observations "
                       "where user_id=%s and id=%s", (self.uid, obs_id))
        return self._obs_from_row(list(row)) if row else None

    def get_observation_by_source(self, source_id: str) -> schema.Observation | None:
        row = self._q1(f"select {self._OBS_COLS} from public.observations "
                       "where user_id=%s and source_id=%s", (self.uid, source_id))
        return self._obs_from_row(list(row)) if row else None

    def update_observation_text(self, obs_id: str, text: str) -> bool:
        return self._x("update public.observations set text=%s where user_id=%s and id=%s",
                       (text, self.uid, obs_id)) == 1

    # -- Entity ------------------------------------------------------------

    def find_entity(self, kind, name, aliases=None):
        name_norm = name.strip()
        aliases = [a.strip() for a in (aliases or []) if a.strip()]
        rows = self._q("select id, name, aliases, recorded_at, description "
                       "from public.entities where user_id=%s and kind=%s order by seq",
                       (self.uid, kind))
        for row in rows:
            existing_aliases = schema.parse_json_list(row[2])
            known = {row[1].strip(), *existing_aliases}
            if name_norm in known or (set(aliases) & known):
                return schema.Entity(id=row[0], kind=kind, name=row[1],
                                     aliases=existing_aliases, recorded_at=row[3],
                                     description=row[4] or "")
        return None

    def upsert_entity(self, kind, name, aliases=None, mention=None):
        name_norm = name.strip()
        aliases = [a.strip() for a in (aliases or []) if a.strip()]
        existing = self.find_entity(kind, name_norm, aliases)
        if existing is not None:
            merged = sorted(
                (set(existing.aliases) | set(aliases) | {name_norm})
                - {existing.name.strip()})
            desc = self._accumulate_description(existing.description, mention)
            self._x("update public.entities set aliases=%s, description=%s "
                    "where user_id=%s and id=%s",
                    (schema.json_list(merged), desc, self.uid, existing.id))
            existing.aliases = merged
            existing.description = desc
            return existing
        ent = schema.Entity(id=schema.new_id("ent"), kind=kind, name=name_norm,
                            aliases=aliases,
                            description=self._accumulate_description("", mention))
        self._insert(ENTITY, {"id": ent.id, "kind": kind, "name": name_norm,
                              "aliases": schema.json_list(aliases),
                              "recorded_at": ent.recorded_at,
                              "description": ent.description})
        return ent

    # -- other nodes -------------------------------------------------------

    def add_context(self, ctx):
        self._insert(CONTEXT, {"id": ctx.id, "kind": ctx.kind, "value": ctx.value,
                               "valid_from": ctx.valid_from, "valid_to": ctx.valid_to,
                               "recorded_at": ctx.recorded_at})

    def add_preference(self, pref):
        self._insert(PREFERENCE, {"id": pref.id, "target": pref.target,
                                  "direction": pref.direction,
                                  "strength_evidence": pref.strength_evidence,
                                  "valid_from": pref.valid_from, "valid_to": pref.valid_to,
                                  "recorded_at": pref.recorded_at})

    def add_criterion(self, crit):
        self._insert(CRITERION, {"id": crit.id, "statement": crit.statement,
                                 "applies_to": crit.applies_to,
                                 "recorded_at": crit.recorded_at})

    def add_hypothesis(self, hyp):
        schema.validate_hypothesis(hyp)
        self._insert(HYPOTHESIS, {
            "id": hyp.id, "conditions": schema.json_list(hyp.conditions),
            "statement": hyp.statement, "status": hyp.status,
            "use_policy": hyp.use_policy, "domain": hyp.domain,
            "valid_from": hyp.valid_from, "valid_to": hyp.valid_to,
            "recorded_at": hyp.recorded_at,
            "status_changed_at": hyp.status_changed_at or hyp.recorded_at})

    def add_style(self, style):
        self._insert(STYLE, {"id": style.id, "pattern": style.pattern,
                             "example_refs": schema.json_list(style.example_refs),
                             "intent": style.intent, "recorded_at": style.recorded_at})

    def add_correction(self, corr):
        self._insert(CORRECTION, {"id": corr.id, "kind": corr.kind, "target": corr.target,
                                  "text": corr.text, "recorded_at": corr.recorded_at})

    def add_response(self, resp):
        self._insert(RESPONSE, {"id": resp.id, "text": resp.text,
                                "created_at": resp.created_at})

    def get_node(self, kind: str, node_id: str) -> dict | None:
        table, cols = NODES[kind] if kind in NODES else (None, None)
        if table is None:
            raise SchemaError(f"unknown node type: {kind}")
        row = self._q1(f"select {', '.join(cols)} from public.{table} "
                       "where user_id=%s and id=%s", (self.uid, node_id))
        return dict(zip(cols, row)) if row is not None else None

    def delete_node(self, kind: str, node_id: str) -> bool:
        table = _table(kind)
        with self._tx() as c:
            n = c.execute(f"delete from public.{table} where user_id=%s and id=%s",
                          (self.uid, node_id)).rowcount
            if n == 0:
                return False
            c.execute("delete from public.edges where user_id=%s and "
                      "((from_kind=%s and from_id=%s) or (to_kind=%s and to_id=%s))",
                      (self.uid, kind, node_id, kind, node_id))
        return True

    # -- hypothesis status -------------------------------------------------

    def set_hypothesis_status(self, hyp_id, status, valid_to=None) -> bool:
        if status not in schema.HYPOTHESIS_STATUSES:
            raise SchemaError(f"unknown hypothesis status: {status}")
        now = schema.utcnow_iso()
        if valid_to is not None:
            n = self._x("update public.hypotheses set status=%s, valid_to=%s, "
                        "status_changed_at=%s where user_id=%s and id=%s",
                        (status, valid_to, now, self.uid, hyp_id))
        else:
            n = self._x("update public.hypotheses set status=%s, status_changed_at=%s "
                        "where user_id=%s and id=%s", (status, now, self.uid, hyp_id))
        return n == 1

    def set_hypothesis_conditions(self, hyp_id: str, conditions: list[str]) -> bool:
        return self._x("update public.hypotheses set conditions=%s where user_id=%s and id=%s",
                       (schema.json_list(conditions), self.uid, hyp_id)) == 1

    def set_hypothesis_use_policy(self, hyp_id: str, use_policy: str) -> bool:
        hyp = self.get_hypothesis(hyp_id)
        if hyp is None:
            return False
        hyp.use_policy = use_policy
        schema.validate_hypothesis(hyp)
        return self._x("update public.hypotheses set use_policy=%s "
                       "where user_id=%s and id=%s", (use_policy, self.uid, hyp_id)) == 1

    # -- edges -------------------------------------------------------------

    def add_edge(self, edge: Edge) -> None:
        schema.validate_edge(edge)
        if self.get_node(edge.from_kind, edge.from_id) is None:
            raise SchemaError(f"edge endpoint missing: {edge.from_kind} {edge.from_id}")
        if self.get_node(edge.to_kind, edge.to_id) is None:
            raise SchemaError(f"edge endpoint missing: {edge.to_kind} {edge.to_id}")
        if edge.rel in schema.EVIDENCE_REQUIRED:
            if self.get_observation(edge.evidence) is None:
                raise SchemaError(
                    f"{edge.rel} edge evidence is not an Observation: {edge.evidence}")
        self._x("insert into public.edges (user_id, rel, from_kind, from_id, to_kind, "
                "to_id, evidence, phrase, valid_from, valid_to, recorded_at) "
                "values (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                (self.uid, edge.rel, edge.from_kind, edge.from_id, edge.to_kind,
                 edge.to_id, edge.evidence,
                 edge.phrase if edge.rel == schema.RELATED_TO else "",
                 edge.valid_from, edge.valid_to, edge.recorded_at))

    # -- blocked list ------------------------------------------------------

    def block(self, node_id, signature_text, reason) -> None:
        self._x("insert into public.blocked_understandings "
                "(user_id, id, signature, reason, recorded_at) values (%s,%s,%s,%s,%s) "
                "on conflict (user_id, id) do update set signature=excluded.signature, "
                "reason=excluded.reason",
                (self.uid, f"block-{node_id}", self.signature(signature_text), reason,
                 schema.utcnow_iso()))

    def unblock(self, node_id) -> bool:
        return self._x("delete from public.blocked_understandings "
                       "where user_id=%s and id=%s",
                       (self.uid, f"block-{node_id}")) == 1

    def is_blocked(self, node_id="", signature_text="") -> bool:
        if node_id and self._q1(
                "select 1 from public.blocked_understandings where user_id=%s and id=%s",
                (self.uid, f"block-{node_id}")):
            return True
        if signature_text and self._q1(
                "select 1 from public.blocked_understandings "
                "where user_id=%s and signature=%s",
                (self.uid, self.signature(signature_text))):
            return True
        return False

    # -- lookups -----------------------------------------------------------

    def _obs_ids_for(self, rel: str, target_id: str, as_of: str | None) -> list[str]:
        sql = ("select o.id from public.edges e join public.observations o "
               "on o.user_id=e.user_id and o.id=e.from_id "
               "where e.user_id=%s and e.rel=%s and e.from_kind='Observation' "
               "and e.to_id=%s")
        params: list = [self.uid, rel, target_id]
        if as_of is not None:
            sql += (" and o.recorded_at <= %s and e.recorded_at <= %s"
                    " and (e.valid_from is null or e.valid_from <= %s)"
                    " and (e.valid_to is null or %s < e.valid_to)")
            params += [as_of] * 4
        return [r[0] for r in self._q(sql + " order by e.seq", params)]

    def hypotheses_supported_by(self, obs_id: str) -> list[str]:
        return [r[0] for r in self._q(
            "select to_id from public.edges where user_id=%s and rel='SUPPORTS' "
            "and from_kind='Observation' and from_id=%s order by seq",
            (self.uid, obs_id))]

    def alternatives(self, hyp_id: str) -> list[str]:
        return [r[0] for r in self._q(
            "select to_id from public.edges where user_id=%s and rel='ALTERNATIVE_TO' "
            "and from_kind='Hypothesis' and from_id=%s order by seq", (self.uid, hyp_id))]

    def active_hypotheses(self, as_of=None):
        as_of = as_of or schema.utcnow_iso()
        cols = NODES[HYPOTHESIS][1]
        rows = self._q(
            f"select {', '.join(cols)} from public.hypotheses "
            "where user_id=%s and status='active' and recorded_at <= %s "
            "and (valid_from is null or valid_from <= %s) "
            "and (valid_to is null or %s < valid_to) order by seq",
            (self.uid, as_of, as_of, as_of))
        return [schema.Hypothesis(
            id=r[0], conditions=schema.parse_json_list(r[1]), statement=r[2],
            status=r[3], use_policy=r[4], domain=r[5], valid_from=r[6], valid_to=r[7],
            recorded_at=r[8], status_changed_at=r[9] or "") for r in rows]

    def search_observations(self, keywords, as_of=None, limit=20):
        as_of = as_of or schema.utcnow_iso()
        rows = self._q(f"select {self._OBS_COLS} from public.observations "
                       "where user_id=%s and recorded_at <= %s order by seq",
                       (self.uid, as_of))
        scored = []
        for row in rows:
            score = sum(1 for kw in keywords if kw and kw in (row[2] or ""))
            if score > 0:
                scored.append((score, row))
        scored.sort(key=lambda t: t[0], reverse=True)
        return [self._obs_from_row(list(row)) for _, row in scored[:limit]]

    def neighbors(self, kind: str, node_id: str) -> dict:
        node = self.get_node(kind, node_id)
        if node is None:
            return {"node": None, "out_edges": [], "in_edges": []}
        out_edges, in_edges = [], []
        for rel, pairs in schema.EDGE_ENDPOINTS.items():
            has_phrase = rel == schema.RELATED_TO
            extra = ", phrase" if has_phrase else ""
            for from_kind, to_kind in pairs:
                if from_kind == kind:
                    for r in self._q(
                            f"select to_id, evidence, recorded_at{extra} from public.edges "
                            "where user_id=%s and rel=%s and from_kind=%s and from_id=%s "
                            "and to_kind=%s order by seq",
                            (self.uid, rel, kind, node_id, to_kind)):
                        out_edges.append({"rel": rel, "to_kind": to_kind, "to_id": r[0],
                                          "evidence": r[1], "recorded_at": r[2],
                                          **({"phrase": r[3]} if has_phrase else {})})
                if to_kind == kind:
                    for r in self._q(
                            f"select from_id, evidence, recorded_at{extra} from public.edges "
                            "where user_id=%s and rel=%s and to_kind=%s and to_id=%s "
                            "and from_kind=%s order by seq",
                            (self.uid, rel, kind, node_id, from_kind)):
                        in_edges.append({"rel": rel, "from_kind": from_kind,
                                         "from_id": r[0], "evidence": r[1],
                                         "recorded_at": r[2],
                                         **({"phrase": r[3]} if has_phrase else {})})
        return {"node": dict(node, kind=kind), "out_edges": out_edges,
                "in_edges": in_edges}

    # -- relation vocabulary ----------------------------------------------

    def relation_vocabulary(self) -> list[dict]:
        rows = self._q("select phrase, from_id, to_id, evidence from public.edges "
                       "where user_id=%s and rel='RELATED_TO' and from_kind='Entity' "
                       "order by seq", (self.uid,))
        vocab: dict[str, dict] = {}
        for phrase, src, dst, ev in rows:
            key = schema.normalize_phrase(phrase or "")
            if not key:
                continue
            entry = vocab.setdefault(key, {"phrase": key, "count": 0, "examples": []})
            entry["count"] += 1
            if len(entry["examples"]) < 3:
                entry["examples"].append({"from": src, "to": dst, "evidence": ev})
        return sorted(vocab.values(), key=lambda d: d["count"], reverse=True)

    def related_entity_names(self, names: list[str]) -> list[str]:
        out: set[str] = set()
        for name in names:
            for side_a, side_b in (("from_id", "to_id"), ("to_id", "from_id")):
                out.update(r[0] for r in self._q(
                    "select b.name from public.edges e "
                    f"join public.entities a on a.user_id=e.user_id and a.id=e.{side_a} "
                    f"join public.entities b on b.user_id=e.user_id and b.id=e.{side_b} "
                    "where e.user_id=%s and e.rel='RELATED_TO' and e.from_kind='Entity' "
                    "and e.to_kind='Entity' and a.name=%s", (self.uid, name)))
        return sorted(out)

    # -- entity merge ------------------------------------------------------

    def merge_entities(self, keep_id: str, drop_id: str) -> bool:
        keep = self.get_node(ENTITY, keep_id)
        drop = self.get_node(ENTITY, drop_id)
        if keep is None or drop is None or keep_id == drop_id:
            return False
        merged = sorted(set(schema.parse_json_list(keep["aliases"]))
                        | set(schema.parse_json_list(drop["aliases"]))
                        | {drop["name"].strip()})
        if keep["name"].strip() in merged:
            merged.remove(keep["name"].strip())
        desc = self._accumulate_description(keep.get("description") or "",
                                            drop.get("description") or "")
        self._x("update public.entities set aliases=%s, description=%s "
                "where user_id=%s and id=%s",
                (schema.json_list(merged), desc, self.uid, keep_id))
        moved = self._q(
            "select to_id, evidence, valid_from, valid_to, recorded_at from public.edges "
            "where user_id=%s and rel='OBSERVED_IN' and from_kind='Entity' and from_id=%s "
            "order by seq", (self.uid, drop_id))
        for obs_id, evidence, vf, vt, rec in moved:
            if self._q1("select 1 from public.edges where user_id=%s and "
                        "rel='OBSERVED_IN' and from_kind='Entity' and from_id=%s "
                        "and to_id=%s", (self.uid, keep_id, obs_id)) is None:
                self._x("insert into public.edges (user_id, rel, from_kind, from_id, "
                        "to_kind, to_id, evidence, valid_from, valid_to, recorded_at) "
                        "values (%s,'OBSERVED_IN','Entity',%s,'Observation',%s,%s,%s,%s,%s)",
                        (self.uid, keep_id, obs_id, evidence, vf, vt, rec))
        self.delete_node(ENTITY, drop_id)
        return True

    # -- predictions / interventions ---------------------------------------

    def log_prediction(self, hyp_id, episode_id, predicted, actual) -> str:
        pid = schema.new_id("pred")
        self._x("insert into public.predictions (user_id, id, hyp_id, episode_id, "
                "predicted, actual, hit, recorded_at) values (%s,%s,%s,%s,%s,%s,%s,%s)",
                (self.uid, pid, hyp_id, episode_id, predicted, actual,
                 predicted == actual, schema.utcnow_iso()))
        return pid

    def hit_stats(self, hyp_id: str) -> dict:
        rows = self._q("select hit from public.predictions where user_id=%s and hyp_id=%s",
                       (self.uid, hyp_id))
        hits = sum(1 for r in rows if r[0])
        return {"n": len(rows), "hits": hits,
                "rate": (hits / len(rows)) if rows else None}

    def log_intervention(self, topic, level, recorded_at=None) -> str:
        iid = schema.new_id("int")
        self._x("insert into public.intervention_logs (user_id, id, topic, level, "
                "recorded_at) values (%s,%s,%s,%s,%s)",
                (self.uid, iid, topic, level, recorded_at or schema.utcnow_iso()))
        return iid

    def recent_interventions(self, topic: str, since: str) -> list[dict]:
        rows = self._q("select level, recorded_at from public.intervention_logs "
                       "where user_id=%s and topic=%s and recorded_at >= %s order by seq",
                       (self.uid, topic, since))
        return [{"level": r[0], "recorded_at": r[1]} for r in rows]

    # -- identity lookups ---------------------------------------------------

    def _find_by_signature(self, table: str, id_col: str, text_col: str, text: str,
                           where: str = "", params: tuple = ()) -> str | None:
        want = self.signature(text)
        rows = self._q(f"select {id_col}, {text_col} from public.{table} "
                       f"where user_id=%s {where} order by seq", (self.uid, *params))
        for rid, value in rows:
            if self.signature(value or "") == want:
                return rid
        return None

    def find_hypothesis_by_statement(self, statement: str) -> str | None:
        return self._find_by_signature("hypotheses", "id", "statement", statement)

    def find_preference(self, target: str, direction: str) -> str | None:
        return self._find_by_signature("preferences", "id", "target", target,
                                       "and direction=%s", (direction,))

    def find_criterion(self, statement: str) -> str | None:
        return self._find_by_signature("criteria", "id", "statement", statement)

    def find_style(self, pattern: str, intent: str) -> str | None:
        row = self._q1("select id from public.styles where user_id=%s and pattern=%s "
                       "and intent=%s order by seq", (self.uid, pattern, intent))
        return row[0] if row else None

    def find_context(self, kind: str, value: str) -> str | None:
        row = self._q1("select id from public.contexts where user_id=%s and kind=%s "
                       "and value=%s order by seq", (self.uid, kind, value))
        return row[0] if row else None

    # -- high level reads ---------------------------------------------------

    def count_nodes(self, kind: str) -> int:
        row = self._q1(f"select count(*) from public.{_table(kind)} where user_id=%s",
                       (self.uid,))
        return int(row[0])

    def node_ids(self, kind: str) -> list[str]:
        return [r[0] for r in self._q(
            f"select id from public.{_table(kind)} where user_id=%s order by seq",
            (self.uid,))]

    def edge_rows(self, rel: str, from_kind: str, to_kind: str) -> list[list]:
        if (from_kind, to_kind) not in schema.EDGE_ENDPOINTS.get(rel, frozenset()):
            raise SchemaError(f"{rel} cannot connect {from_kind} -> {to_kind}")
        return [list(r) for r in self._q(
            "select from_id, to_id, evidence from public.edges where user_id=%s "
            "and rel=%s and from_kind=%s and to_kind=%s order by seq",
            (self.uid, rel, from_kind, to_kind))]

    def observation_rows(self) -> list[list]:
        return [list(r) for r in self._q(
            "select id, source_id, occurred_at, text from public.observations "
            "where user_id=%s order by occurred_at, seq", (self.uid,))]

    def observation_texts(self) -> list[list]:
        return [list(r) for r in self._q(
            "select text, occurred_at from public.observations "
            "where user_id=%s order by occurred_at, seq", (self.uid,))]

    def recent_observations(self, limit: int) -> list[list]:
        return [list(r) for r in self._q(
            "select id, text, occurred_at, source_id from public.observations "
            "where user_id=%s order by recorded_at desc, seq desc limit %s",
            (self.uid, limit))]

    def observation_links(self, obs_id: str) -> dict:
        def names(table: str, col: str, kind: str) -> list[str]:
            return [r[0] for r in self._q(
                f"select n.{col} from public.edges e join public.{table} n "
                "on n.user_id=e.user_id and n.id=e.from_id "
                "where e.user_id=%s and e.rel='OBSERVED_IN' and e.from_kind=%s "
                "and e.to_kind='Observation' and e.to_id=%s order by e.seq",
                (self.uid, kind, obs_id))]
        return {
            "entities": names("entities", "name", ENTITY),
            "contexts": names("contexts", "value", CONTEXT),
            "hypotheses": [r[0] for r in self._q(
                "select h.statement from public.edges e join public.hypotheses h "
                "on h.user_id=e.user_id and h.id=e.to_id "
                "where e.user_id=%s and e.rel='SUPPORTS' and e.from_kind='Observation' "
                "and e.from_id=%s and e.to_kind='Hypothesis' order by e.seq",
                (self.uid, obs_id))],
        }

    def hypothesis_statements(self) -> list[str]:
        return [r[0] for r in self._q(
            "select statement from public.hypotheses where user_id=%s order by seq",
            (self.uid,))]

    def hypothesis_catalog(self) -> list[dict]:
        rows = self._q(
            "select id, statement, status, conditions from public.hypotheses "
            "where user_id=%s and status in ('candidate', 'active') order by seq", (self.uid,))
        return [{"id": r[0], "statement": r[1], "status": r[2],
                 "conditions": schema.parse_json_list(r[3])} for r in rows]

    def hypothesis_brief(self, limit: int) -> list[list]:
        return [list(r) for r in self._q(
            "select statement, status from public.hypotheses where user_id=%s "
            "order by seq limit %s", (self.uid, limit))]

    def hypothesis_ids_by_status(self, statuses: list[str], as_of: str) -> list[str]:
        return [r[0] for r in self._q(
            "select id from public.hypotheses where user_id=%s and status = any(%s) "
            "and recorded_at <= %s order by seq", (self.uid, list(statuses), as_of))]

    def hypotheses_recorded_between(self, since: str, as_of: str) -> list[list]:
        return [list(r) for r in self._q(
            "select id, statement, status, use_policy, domain from public.hypotheses "
            "where user_id=%s and recorded_at >= %s and recorded_at <= %s order by seq",
            (self.uid, since, as_of))]

    def hypotheses_changed_between(self, since: str, as_of: str) -> list[list]:
        return [list(r) for r in self._q(
            "select id, statement, status from public.hypotheses where user_id=%s "
            "and status_changed_at >= %s and status_changed_at <= %s order by seq",
            (self.uid, since, as_of))]

    def corrections_between(self, since: str, as_of: str) -> list[list]:
        return [list(r) for r in self._q(
            "select id, kind, target, text from public.corrections where user_id=%s "
            "and recorded_at >= %s and recorded_at <= %s order by seq",
            (self.uid, since, as_of))]

    def _observed_counts(self, table: str, cols: str, kind: str) -> list[list]:
        return [list(r) for r in self._q(
            f"select {', '.join('n.' + c for c in cols.split(','))}, count(*) "
            "from public.edges e join public." + table + " n "
            "on n.user_id=e.user_id and n.id=e.from_id "
            "where e.user_id=%s and e.rel='OBSERVED_IN' and e.from_kind=%s "
            "and e.to_kind='Observation' "
            f"group by {', '.join('n.' + c for c in cols.split(','))} "
            "order by min(e.seq)", (self.uid, kind))]

    def entity_observation_counts(self) -> list[list]:
        return self._observed_counts("entities", "name,kind", ENTITY)

    def context_observation_counts(self) -> list[list]:
        return self._observed_counts("contexts", "kind,value", CONTEXT)

    def preference_rows(self) -> list[list]:
        return [list(r) for r in self._q(
            "select target, direction, strength_evidence from public.preferences "
            "where user_id=%s order by seq", (self.uid,))]

    def criterion_rows(self) -> list[list]:
        return [list(r) for r in self._q(
            "select statement, applies_to from public.criteria "
            "where user_id=%s order by seq", (self.uid,))]

    def style_rows(self) -> list[list]:
        return [list(r) for r in self._q(
            "select pattern, intent, example_refs from public.styles "
            "where user_id=%s order by seq", (self.uid,))]

    def style_brief(self) -> list[list]:
        return [list(r) for r in self._q(
            "select id, pattern, intent from public.styles where user_id=%s order by seq",
            (self.uid,))]

    def styles_by_intent(self, intent: str) -> list[list]:
        return [list(r) for r in self._q(
            "select id, example_refs from public.styles where user_id=%s and intent=%s "
            "order by seq", (self.uid, intent))]

    def style_ids_by_pattern(self, pattern: str) -> list[str]:
        return [r[0] for r in self._q(
            "select id from public.styles where user_id=%s and pattern=%s order by seq",
            (self.uid, pattern))]

    def entity_aliases(self) -> list[list]:
        return [list(r) for r in self._q(
            "select name, aliases from public.entities where user_id=%s order by seq",
            (self.uid,))]

    def entity_ids(self) -> list[list]:
        return [list(r) for r in self._q(
            "select id, kind, name, recorded_at from public.entities "
            "where user_id=%s order by seq", (self.uid,))]

    def entity_brief(self, limit: int) -> list[list]:
        return [list(r) for r in self._q(
            "select name, kind, description from public.entities where user_id=%s "
            "order by seq limit %s", (self.uid, limit))]

    def cosupported_pairs(self) -> list[list]:
        return [list(r) for r in self._q(
            "select e1.to_id, e2.to_id, e1.from_id, h1.status, h2.status "
            "from public.edges e1 "
            "join public.edges e2 on e2.user_id=e1.user_id and e2.rel='SUPPORTS' "
            "and e2.from_kind='Observation' and e2.from_id=e1.from_id "
            "and e2.to_kind='Hypothesis' "
            "join public.hypotheses h1 on h1.user_id=e1.user_id and h1.id=e1.to_id "
            "join public.hypotheses h2 on h2.user_id=e1.user_id and h2.id=e2.to_id "
            "where e1.user_id=%s and e1.rel='SUPPORTS' and e1.from_kind='Observation' "
            "and e1.to_kind='Hypothesis' and e1.to_id < e2.to_id "
            "order by e1.seq, e2.seq", (self.uid,))]

    def has_alternative(self, a: str, b: str) -> bool:
        return self._q1(
            "select 1 from public.edges where user_id=%s and rel='ALTERNATIVE_TO' "
            "and from_kind='Hypothesis' and from_id=%s and to_kind='Hypothesis' "
            "and to_id=%s", (self.uid, a, b)) is not None
