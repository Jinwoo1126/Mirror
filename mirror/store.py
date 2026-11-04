"""Kuzu 기반 이중 시간 그래프 저장소 (계획서 4·5장).

모든 엣지는 valid_from / valid_to / recorded_at 세 시점을 가진다.
valid_*는 현실 유효 구간, recorded_at은 알게 된 시점이다.
평가 리플레이는 recorded_at <= as_of 조건으로 미래 정보를 차단한다.
"""

from __future__ import annotations

from . import schema
from .schema import (
    CORRECTION,
    CRITERION,
    ENTITY,
    HYPOTHESIS,
    OBSERVATION,
    PREFERENCE,
    RESPONSE,
    STYLE,
    CONTEXT,
    Edge,
    SchemaError,
)

DDL = [
    """CREATE NODE TABLE Observation(
        id STRING, source_id STRING, text STRING, occurred_at STRING,
        raw_ref STRING, visibility STRING, recorded_at STRING, PRIMARY KEY(id))""",
    """CREATE NODE TABLE Entity(
        id STRING, kind STRING, name STRING, aliases STRING,
        recorded_at STRING, description STRING, PRIMARY KEY(id))""",
    """CREATE NODE TABLE Context(
        id STRING, kind STRING, value STRING,
        valid_from STRING, valid_to STRING, recorded_at STRING, PRIMARY KEY(id))""",
    """CREATE NODE TABLE Preference(
        id STRING, target STRING, direction STRING, strength_evidence STRING,
        valid_from STRING, valid_to STRING, recorded_at STRING, PRIMARY KEY(id))""",
    """CREATE NODE TABLE Criterion(
        id STRING, statement STRING, applies_to STRING,
        recorded_at STRING, PRIMARY KEY(id))""",
    """CREATE NODE TABLE Hypothesis(
        id STRING, conditions STRING, statement STRING, status STRING,
        use_policy STRING, domain STRING,
        valid_from STRING, valid_to STRING, recorded_at STRING,
        status_changed_at STRING, PRIMARY KEY(id))""",
    """CREATE NODE TABLE Style(
        id STRING, pattern STRING, example_refs STRING, intent STRING,
        recorded_at STRING, PRIMARY KEY(id))""",
    """CREATE NODE TABLE Correction(
        id STRING, kind STRING, target STRING, text STRING,
        recorded_at STRING, PRIMARY KEY(id))""",
    """CREATE NODE TABLE Response(
        id STRING, text STRING, created_at STRING, PRIMARY KEY(id))""",
    "CREATE NODE TABLE Meta(key STRING, value STRING, PRIMARY KEY(key))",
    """CREATE NODE TABLE BlockedUnderstanding(
        id STRING, signature STRING, reason STRING, recorded_at STRING,
        PRIMARY KEY(id))""",
    """CREATE NODE TABLE Prediction(
        id STRING, hyp_id STRING, episode_id STRING,
        predicted STRING, actual STRING, hit BOOL, recorded_at STRING,
        PRIMARY KEY(id))""",
    """CREATE NODE TABLE InterventionLog(
        id STRING, topic STRING, level INT64, recorded_at STRING,
        PRIMARY KEY(id))""",
    """CREATE REL TABLE OBSERVED_IN(
        FROM Entity TO Observation, FROM Context TO Observation,
        evidence STRING, valid_from STRING, valid_to STRING, recorded_at STRING)""",
    """CREATE REL TABLE SUPPORTS(
        FROM Observation TO Hypothesis, FROM Observation TO Preference,
        evidence STRING, valid_from STRING, valid_to STRING, recorded_at STRING)""",
    """CREATE REL TABLE CONTRADICTS(
        FROM Observation TO Hypothesis, FROM Observation TO Preference,
        evidence STRING, valid_from STRING, valid_to STRING, recorded_at STRING)""",
    """CREATE REL TABLE CONDITIONED_ON(
        FROM Hypothesis TO Context,
        evidence STRING, valid_from STRING, valid_to STRING, recorded_at STRING)""",
    """CREATE REL TABLE ALTERNATIVE_TO(
        FROM Hypothesis TO Hypothesis,
        evidence STRING, valid_from STRING, valid_to STRING, recorded_at STRING)""",
    """CREATE REL TABLE DERIVED_FROM(
        FROM Criterion TO Hypothesis, FROM Preference TO Hypothesis,
        evidence STRING, valid_from STRING, valid_to STRING, recorded_at STRING)""",
    """CREATE REL TABLE CORRECTS(
        FROM Correction TO Observation, FROM Correction TO Hypothesis,
        evidence STRING, valid_from STRING, valid_to STRING, recorded_at STRING)""",
    """CREATE REL TABLE USED_IN(
        FROM Hypothesis TO Response, FROM Style TO Response,
        evidence STRING, valid_from STRING, valid_to STRING, recorded_at STRING)""",
    """CREATE REL TABLE RELATED_TO(
        FROM Entity TO Entity, FROM Entity TO Context,
        evidence STRING, phrase STRING,
        valid_from STRING, valid_to STRING, recorded_at STRING)""",
]


class MirrorStore:
    """Mirror 그래프 저장소. path는 Kuzu DB 디렉터리."""

    def __init__(self, path: str):
        import kuzu  # imported here so a hosted deployment does not need it

        self.path = path
        self._db = kuzu.Database(path)
        self._conn = kuzu.Connection(self._db)
        self._ensure_schema()

    # -- 스키마 ----------------------------------------------------------

    def _tables(self) -> set[str]:
        try:
            result = self._conn.execute("CALL SHOW_TABLES() RETURN name")
        except Exception:
            return set()
        names = set()
        while result.has_next():
            names.add(str(result.get_next()[0]))
        return names

    def _ensure_schema(self) -> None:
        existing = self._tables()
        for stmt in DDL:
            # "CREATE NODE TABLE X(" / "CREATE REL TABLE X(" 에서 이름 추출
            name = stmt.split("TABLE", 1)[1].split("(", 1)[0].strip()
            if name not in existing:
                self._conn.execute(stmt)
        self._migrate()

    def _migrate(self) -> None:
        """v1 → v2(Hypothesis.status_changed_at) → v3(Entity.description)."""
        row = self._one("MATCH (m:Meta {key: 'schema_v'}) RETURN m.value")
        version = int(row[0]) if row is not None else 0
        if version < 2:
            self._add_column("Hypothesis", "status_changed_at STRING")
            version = 2
        if version < 3:
            self._add_column("Entity", "description STRING")
            version = 3
        self._conn.execute(
            "MERGE (m:Meta {key: 'schema_v'}) ON MATCH SET m.value = $v "
            "ON CREATE SET m.value = $v",
            {"v": str(version)},
        )

    def _add_column(self, table: str, column: str) -> None:
        try:
            self._conn.execute(f"ALTER TABLE {table} ADD {column}")
        except Exception as e:
            if "already has property" not in str(e).lower():
                raise

    def close(self) -> None:
        self._conn = None  # type: ignore[assignment]
        self._db = None  # type: ignore[assignment]

    # -- 저수준 헬퍼 ------------------------------------------------------

    def _rows(self, query: str, params: dict | None = None) -> list[list]:
        result = self._conn.execute(query, params or {})
        out = []
        while result.has_next():
            out.append(result.get_next())
        return out

    def _one(self, query: str, params: dict | None = None) -> list | None:
        rows = self._rows(query, params)
        return rows[0] if rows else None

    # -- revision ----------------------------------------------------------

    def get_revision(self) -> int:
        row = self._one("MATCH (m:Meta {key: 'revision'}) RETURN m.value")
        return int(row[0]) if row else 0

    def bump_revision(self) -> int:
        rev = self.get_revision() + 1
        self._conn.execute(
            "MERGE (m:Meta {key: 'revision'}) ON MATCH SET m.value = $v "
            "ON CREATE SET m.value = $v",
            {"v": str(rev)},
        )
        return rev

    # -- Observation -------------------------------------------------------

    def add_observation(self, obs: schema.Observation) -> bool:
        """추가 성공 시 True, source_id 중복이면 False."""
        if self.get_observation_by_source(obs.source_id) is not None:
            return False
        self._conn.execute(
            "CREATE (o:Observation {id: $id, source_id: $sid, text: $text, "
            "occurred_at: $occ, raw_ref: $ref, visibility: $vis, recorded_at: $rec})",
            {
                "id": obs.id,
                "sid": obs.source_id,
                "text": obs.text,
                "occ": obs.occurred_at,
                "ref": obs.raw_ref,
                "vis": obs.visibility,
                "rec": obs.recorded_at,
            },
        )
        return True

    def _obs_from_row(self, row: list) -> schema.Observation:
        return schema.Observation(
            id=row[0], source_id=row[1], text=row[2], occurred_at=row[3],
            raw_ref=row[4] or "", visibility=row[5] or "private",
            recorded_at=row[6],
        )

    def get_observation(self, obs_id: str) -> schema.Observation | None:
        row = self._one(
            "MATCH (o:Observation {id: $id}) RETURN o.id, o.source_id, o.text, "
            "o.occurred_at, o.raw_ref, o.visibility, o.recorded_at",
            {"id": obs_id},
        )
        return self._obs_from_row(row) if row else None

    def update_observation_text(self, obs_id: str, text: str) -> bool:
        """사실 정정용 원문 수정. 없으면 False."""
        if self.get_observation(obs_id) is None:
            return False
        self._conn.execute(
            "MATCH (o:Observation {id: $id}) SET o.text = $text",
            {"id": obs_id, "text": text},
        )
        return True

    def get_observation_by_source(self, source_id: str) -> schema.Observation | None:
        row = self._one(
            "MATCH (o:Observation {source_id: $sid}) RETURN o.id, o.source_id, o.text, "
            "o.occurred_at, o.raw_ref, o.visibility, o.recorded_at",
            {"sid": source_id},
        )
        return self._obs_from_row(row) if row else None

    # -- Entity (병합) -----------------------------------------------------

    def find_entity(
        self, kind: str, name: str, aliases: list[str] | None = None
    ) -> schema.Entity | None:
        """kind+name 또는 별칭이 겹치는 기존 엔티티."""
        name_norm = name.strip()
        aliases = [a.strip() for a in (aliases or []) if a.strip()]
        rows = self._rows(
            "MATCH (e:Entity {kind: $kind}) "
            "RETURN e.id, e.name, e.aliases, e.recorded_at, e.description",
            {"kind": kind},
        )
        for row in rows:
            existing_aliases = schema.parse_json_list(row[2])
            known = {row[1].strip(), *existing_aliases}
            if name_norm in known or (set(aliases) & known):
                return schema.Entity(
                    id=row[0], kind=kind, name=row[1],
                    aliases=existing_aliases, recorded_at=row[3],
                    description=row[4] or "",
                )
        return None

    @staticmethod
    def _accumulate_description(current: str, mention: str | None) -> str:
        if not mention:
            return current or ""
        sentence = " ".join(mention.strip().split())[:200]
        if not sentence or sentence in (current or ""):
            return current or ""
        combined = ((current or "") + " " + sentence).strip()
        return combined[:1000]

    def upsert_entity(
        self, kind: str, name: str, aliases: list[str] | None = None,
        mention: str | None = None,
    ) -> schema.Entity:
        """kind+name 또는 별칭이 겹치면 기존 엔티티에 병합.

        mention이 주어지면 엔티티 요약(description)에 누적한다.
        """
        name_norm = name.strip()
        aliases = [a.strip() for a in (aliases or []) if a.strip()]
        existing = self.find_entity(kind, name_norm, aliases)
        if existing is not None:
            # 별칭으로 찾은 경우 새 이름이 대표 이름과 다를 수 있다
            merged = sorted(
                (set(existing.aliases) | set(aliases) | {name_norm})
                - {existing.name.strip()})
            desc = self._accumulate_description(existing.description, mention)
            self._conn.execute(
                "MATCH (e:Entity {id: $id}) "
                "SET e.aliases = $aliases, e.description = $descr",
                {"id": existing.id, "aliases": schema.json_list(merged),
                 "descr": desc},
            )
            existing.aliases = merged
            existing.description = desc
            return existing
        ent = schema.Entity(
            id=schema.new_id("ent"), kind=kind, name=name_norm, aliases=aliases,
            description=self._accumulate_description("", mention),
        )
        self._conn.execute(
            "CREATE (e:Entity {id: $id, kind: $kind, name: $name, "
            "aliases: $aliases, recorded_at: $rec, description: $descr})",
            {
                "id": ent.id, "kind": kind, "name": name_norm,
                "aliases": schema.json_list(aliases), "rec": ent.recorded_at,
                "descr": ent.description,
            },
        )
        return ent

    # -- 나머지 노드 --------------------------------------------------------

    def add_context(self, ctx: schema.Context) -> None:
        self._conn.execute(
            "CREATE (c:Context {id: $id, kind: $kind, value: $v, "
            "valid_from: $vf, valid_to: $vt, recorded_at: $rec})",
            {"id": ctx.id, "kind": ctx.kind, "v": ctx.value,
             "vf": ctx.valid_from, "vt": ctx.valid_to, "rec": ctx.recorded_at},
        )

    def add_preference(self, pref: schema.Preference) -> None:
        self._conn.execute(
            "CREATE (p:Preference {id: $id, target: $t, direction: $d, "
            "strength_evidence: $se, valid_from: $vf, valid_to: $vt, recorded_at: $rec})",
            {"id": pref.id, "t": pref.target, "d": pref.direction,
             "se": pref.strength_evidence, "vf": pref.valid_from,
             "vt": pref.valid_to, "rec": pref.recorded_at},
        )

    def add_criterion(self, crit: schema.Criterion) -> None:
        self._conn.execute(
            "CREATE (c:Criterion {id: $id, statement: $s, applies_to: $a, recorded_at: $rec})",
            {"id": crit.id, "s": crit.statement, "a": crit.applies_to,
             "rec": crit.recorded_at},
        )

    def add_hypothesis(self, hyp: schema.Hypothesis) -> None:
        schema.validate_hypothesis(hyp)
        self._conn.execute(
            "CREATE (h:Hypothesis {id: $id, conditions: $c, statement: $s, "
            "status: $st, use_policy: $up, domain: $d, "
            "valid_from: $vf, valid_to: $vt, recorded_at: $rec, "
            "status_changed_at: $sca})",
            {"id": hyp.id, "c": schema.json_list(hyp.conditions), "s": hyp.statement,
             "st": hyp.status, "up": hyp.use_policy, "d": hyp.domain,
             "vf": hyp.valid_from, "vt": hyp.valid_to, "rec": hyp.recorded_at,
             "sca": hyp.status_changed_at or hyp.recorded_at},
        )

    def add_style(self, style: schema.Style) -> None:
        self._conn.execute(
            "CREATE (s:Style {id: $id, pattern: $p, example_refs: $e, "
            "intent: $i, recorded_at: $rec})",
            {"id": style.id, "p": style.pattern,
             "e": schema.json_list(style.example_refs), "i": style.intent,
             "rec": style.recorded_at},
        )

    def add_correction(self, corr: schema.Correction) -> None:
        self._conn.execute(
            "CREATE (c:Correction {id: $id, kind: $k, target: $t, "
            "text: $x, recorded_at: $rec})",
            {"id": corr.id, "k": corr.kind, "t": corr.target,
             "x": corr.text, "rec": corr.recorded_at},
        )

    def add_response(self, resp: schema.Response) -> None:
        self._conn.execute(
            "CREATE (r:Response {id: $id, text: $t, created_at: $c})",
            {"id": resp.id, "t": resp.text, "c": resp.created_at},
        )

    def get_node(self, kind: str, node_id: str) -> dict | None:
        """노드 1개를 dict로 조회. 없으면 None."""
        if kind not in schema.NODE_TYPES:
            raise SchemaError(f"unknown node type: {kind}")
        cols = {
            OBSERVATION: ["id", "source_id", "text", "occurred_at", "raw_ref",
                          "visibility", "recorded_at"],
            ENTITY: ["id", "kind", "name", "aliases", "recorded_at",
                     "description"],
            CONTEXT: ["id", "kind", "value", "valid_from", "valid_to", "recorded_at"],
            PREFERENCE: ["id", "target", "direction", "strength_evidence",
                         "valid_from", "valid_to", "recorded_at"],
            CRITERION: ["id", "statement", "applies_to", "recorded_at"],
            HYPOTHESIS: ["id", "conditions", "statement", "status", "use_policy",
                         "domain", "valid_from", "valid_to", "recorded_at",
                         "status_changed_at"],
            STYLE: ["id", "pattern", "example_refs", "intent", "recorded_at"],
            CORRECTION: ["id", "kind", "target", "text", "recorded_at"],
            RESPONSE: ["id", "text", "created_at"],
        }[kind]
        proj = ", ".join(f"n.{c}" for c in cols)
        row = self._one(
            f"MATCH (n:{kind} {{id: $id}}) RETURN {proj}", {"id": node_id}
        )
        if row is None:
            return None
        return dict(zip(cols, row))

    def delete_node(self, kind: str, node_id: str) -> bool:
        if self.get_node(kind, node_id) is None:
            return False
        self._conn.execute(
            f"MATCH (n:{kind} {{id: $id}}) DETACH DELETE n", {"id": node_id}
        )
        return True

    # -- 가설 상태 ----------------------------------------------------------

    def get_hypothesis(self, hyp_id: str) -> schema.Hypothesis | None:
        d = self.get_node(HYPOTHESIS, hyp_id)
        if d is None:
            return None
        return schema.Hypothesis(
            id=d["id"], conditions=schema.parse_json_list(d["conditions"]),
            statement=d["statement"], status=d["status"], use_policy=d["use_policy"],
            domain=d["domain"], valid_from=d["valid_from"], valid_to=d["valid_to"],
            recorded_at=d["recorded_at"],
            status_changed_at=d.get("status_changed_at") or "",
        )

    def set_hypothesis_status(
        self, hyp_id: str, status: str, valid_to: str | None = None
    ) -> bool:
        if status not in schema.HYPOTHESIS_STATUSES:
            raise SchemaError(f"unknown hypothesis status: {status}")
        if self.get_node(HYPOTHESIS, hyp_id) is None:
            return False
        now = schema.utcnow_iso()
        if valid_to is not None:
            self._conn.execute(
                "MATCH (h:Hypothesis {id: $id}) SET h.status = $st, h.valid_to = $vt, "
                "h.status_changed_at = $sca",
                {"id": hyp_id, "st": status, "vt": valid_to, "sca": now},
            )
        else:
            self._conn.execute(
                "MATCH (h:Hypothesis {id: $id}) SET h.status = $st, "
                "h.status_changed_at = $sca",
                {"id": hyp_id, "st": status, "sca": now},
            )
        return True

    def set_hypothesis_conditions(self, hyp_id: str, conditions: list[str]) -> bool:
        if self.get_node(HYPOTHESIS, hyp_id) is None:
            return False
        self._conn.execute("MATCH (h:Hypothesis {id: $id}) SET h.conditions = $c",
                           {"id": hyp_id, "c": schema.json_list(conditions)})
        return True

    def set_hypothesis_use_policy(self, hyp_id: str, use_policy: str) -> bool:
        """Change how a hypothesis may be used. Sensitive domains stay internal_only."""
        hyp = self.get_hypothesis(hyp_id)
        if hyp is None:
            return False
        hyp.use_policy = use_policy
        schema.validate_hypothesis(hyp)
        self._conn.execute(
            "MATCH (h:Hypothesis {id: $id}) SET h.use_policy = $up",
            {"id": hyp_id, "up": use_policy},
        )
        return True

    # -- 엣지 ---------------------------------------------------------------

    def add_edge(self, edge: Edge) -> None:
        """스키마 검증 후 저장. 근거 없는 엣지·없는 엔드포인트는 거부."""
        schema.validate_edge(edge)
        if self.get_node(edge.from_kind, edge.from_id) is None:
            raise SchemaError(
                f"edge endpoint missing: {edge.from_kind} {edge.from_id}"
            )
        if self.get_node(edge.to_kind, edge.to_id) is None:
            raise SchemaError(f"edge endpoint missing: {edge.to_kind} {edge.to_id}")
        if edge.rel in schema.EVIDENCE_REQUIRED:
            if self.get_observation(edge.evidence) is None:
                raise SchemaError(
                    f"{edge.rel} edge evidence is not an Observation: {edge.evidence}"
                )
        if edge.rel == schema.RELATED_TO:
            props = ("{evidence: $ev, phrase: $ph, valid_from: $vf, "
                     "valid_to: $vt, recorded_at: $rec}")
            params = {"from": edge.from_id, "to": edge.to_id,
                      "ev": edge.evidence, "ph": edge.phrase,
                      "vf": edge.valid_from, "vt": edge.valid_to,
                      "rec": edge.recorded_at}
        else:
            props = ("{evidence: $ev, valid_from: $vf, "
                     "valid_to: $vt, recorded_at: $rec}")
            params = {"from": edge.from_id, "to": edge.to_id,
                      "ev": edge.evidence, "vf": edge.valid_from,
                      "vt": edge.valid_to, "rec": edge.recorded_at}
        self._conn.execute(
            f"MATCH (a:{edge.from_kind} {{id: $from}}) "
            f"MATCH (b:{edge.to_kind} {{id: $to}}) "
            f"CREATE (a)-[e:{edge.rel} {props}]->(b)",
            params,
        )

    # -- 차단 목록 (삭제된 이해의 재출현 방지) --------------------------------

    @staticmethod
    def signature(text: str) -> str:
        return " ".join(text.strip().lower().split())

    def block(self, node_id: str, signature_text: str, reason: str) -> None:
        self._conn.execute(
            "MERGE (b:BlockedUnderstanding {id: $id}) "
            "ON MATCH SET b.signature = $sig, b.reason = $r "
            "ON CREATE SET b.signature = $sig, b.reason = $r, b.recorded_at = $rec",
            {"id": f"block-{node_id}", "sig": self.signature(signature_text),
             "r": reason, "rec": schema.utcnow_iso()},
        )

    def unblock(self, node_id: str) -> bool:
        row = self._one(
            "MATCH (b:BlockedUnderstanding {id: $id}) RETURN b.id",
            {"id": f"block-{node_id}"},
        )
        if row is None:
            return False
        self._conn.execute(
            "MATCH (b:BlockedUnderstanding {id: $id}) DELETE b",
            {"id": f"block-{node_id}"},
        )
        return True

    def is_blocked(self, node_id: str = "", signature_text: str = "") -> bool:
        if node_id:
            row = self._one(
                "MATCH (b:BlockedUnderstanding {id: $id}) RETURN b.id",
                {"id": f"block-{node_id}"},
            )
            if row is not None:
                return True
        if signature_text:
            row = self._one(
                "MATCH (b:BlockedUnderstanding) WHERE b.signature = $sig RETURN b.id",
                {"sig": self.signature(signature_text)},
            )
            if row is not None:
                return True
        return False

    # -- 조회 -----------------------------------------------------------------

    def supporting_observations(self, hyp_id: str, as_of: str | None = None) -> list[str]:
        return self._obs_ids_for("SUPPORTS", hyp_id, as_of)

    def contradicting_observations(
        self, hyp_id: str, as_of: str | None = None
    ) -> list[str]:
        return self._obs_ids_for("CONTRADICTS", hyp_id, as_of)

    def _obs_ids_for(self, rel: str, target_id: str, as_of: str | None) -> list[str]:
        if as_of is None:
            rows = self._rows(
                f"MATCH (o:Observation)-[e:{rel}]->(h) WHERE h.id = $id "
                "RETURN o.id",
                {"id": target_id},
            )
        else:
            rows = self._rows(
                f"MATCH (o:Observation)-[e:{rel}]->(h) WHERE h.id = $id "
                "AND o.recorded_at <= $as_of AND e.recorded_at <= $as_of "
                "AND (e.valid_from IS NULL OR e.valid_from <= $as_of) "
                "AND (e.valid_to IS NULL OR $as_of < e.valid_to) RETURN o.id",
                {"id": target_id, "as_of": as_of},
            )
        return [r[0] for r in rows]

    def hypotheses_supported_by(self, obs_id: str) -> list[str]:
        """obs가 SUPPORTS로 받치는 가설/선호 (정정 전파 경로)."""
        rows = self._rows(
            "MATCH (o:Observation {id: $id})-[e:SUPPORTS]->(h) RETURN h.id",
            {"id": obs_id},
        )
        return [r[0] for r in rows]

    def alternatives(self, hyp_id: str) -> list[str]:
        rows = self._rows(
            "MATCH (h:Hypothesis {id: $id})-[e:ALTERNATIVE_TO]->(o) RETURN o.id",
            {"id": hyp_id},
        )
        return [r[0] for r in rows]

    def active_hypotheses(self, as_of: str | None = None) -> list[schema.Hypothesis]:
        if as_of is None:
            as_of = schema.utcnow_iso()
        rows = self._rows(
            "MATCH (h:Hypothesis) WHERE h.status = 'active' "
            "AND h.recorded_at <= $as_of "
            "AND (h.valid_from IS NULL OR h.valid_from <= $as_of) "
            "AND (h.valid_to IS NULL OR $as_of < h.valid_to) "
            "RETURN h.id, h.conditions, h.statement, h.status, h.use_policy, "
            "h.domain, h.valid_from, h.valid_to, h.recorded_at, h.status_changed_at",
            {"as_of": as_of},
        )
        return [
            schema.Hypothesis(
                id=r[0], conditions=schema.parse_json_list(r[1]), statement=r[2],
                status=r[3], use_policy=r[4], domain=r[5], valid_from=r[6],
                valid_to=r[7], recorded_at=r[8], status_changed_at=r[9] or "",
            )
            for r in rows
        ]

    def search_observations(
        self, keywords: list[str], as_of: str | None = None, limit: int = 20
    ) -> list[schema.Observation]:
        """키워드 포함 점수순 관찰 검색 (P1: 벡터 없이 문자열 매칭)."""
        if as_of is None:
            as_of = schema.utcnow_iso()
        rows = self._rows(
            "MATCH (o:Observation) WHERE o.recorded_at <= $as_of "
            "RETURN o.id, o.source_id, o.text, o.occurred_at, o.raw_ref, "
            "o.visibility, o.recorded_at",
            {"as_of": as_of},
        )
        scored = []
        for row in rows:
            text = row[2] or ""
            score = sum(1 for kw in keywords if kw and kw in text)
            if score > 0:
                scored.append((score, row))
        scored.sort(key=lambda t: t[0], reverse=True)
        return [self._obs_from_row(row) for _, row in scored[:limit]]

    def neighbors(self, kind: str, node_id: str) -> dict:
        """control query용: 노드 + 입출력 엣지 요약."""
        node = self.get_node(kind, node_id)
        if node is None:
            return {"node": None, "out_edges": [], "in_edges": []}
        out_edges, in_edges = [], []
        for rel, pairs in schema.EDGE_ENDPOINTS.items():
            extra = ", e.phrase" if rel == schema.RELATED_TO else ""
            for from_kind, to_kind in pairs:
                if from_kind == kind:
                    rows = self._rows(
                        f"MATCH (a:{kind} {{id: $id}})-[e:{rel}]->(b:{to_kind}) "
                        f"RETURN b.id, e.evidence, e.recorded_at{extra}",
                        {"id": node_id},
                    )
                    out_edges += [
                        {"rel": rel, "to_kind": to_kind, "to_id": r[0],
                         "evidence": r[1], "recorded_at": r[2],
                         **({"phrase": r[3]} if extra else {})}
                        for r in rows
                    ]
                if to_kind == kind:
                    rows = self._rows(
                        f"MATCH (a:{from_kind})-[e:{rel}]->(b:{kind} {{id: $id}}) "
                        f"RETURN a.id, e.evidence, e.recorded_at{extra}",
                        {"id": node_id},
                    )
                    in_edges += [
                        {"rel": rel, "from_kind": from_kind, "from_id": r[0],
                         "evidence": r[1], "recorded_at": r[2],
                         **({"phrase": r[3]} if extra else {})}
                        for r in rows
                    ]
        return {"node": dict(node, kind=kind), "out_edges": out_edges,
                "in_edges": in_edges}

    # -- 동적 관계 어휘 ------------------------------------------------------

    def relation_vocabulary(self) -> list[dict]:
        """RELATED_TO 서술 어휘 집계 (정규화 서술, 횟수, 예시)."""
        rows = self._rows(
            "MATCH (a:Entity)-[e:RELATED_TO]->(b) "
            "RETURN e.phrase, a.id, b.id, e.evidence")
        vocab: dict[str, dict] = {}
        for phrase, src, dst, ev in rows:
            key = schema.normalize_phrase(phrase or "")
            if not key:
                continue
            entry = vocab.setdefault(
                key, {"phrase": key, "count": 0, "examples": []})
            entry["count"] += 1
            if len(entry["examples"]) < 3:
                entry["examples"].append(
                    {"from": src, "to": dst, "evidence": ev})
        return sorted(vocab.values(), key=lambda d: d["count"], reverse=True)

    def related_entity_names(self, names: list[str]) -> list[str]:
        """RELATED_TO 1홉 이웃 엔티티 이름 (조회 범위 확장용)."""
        out: set[str] = set()
        for name in names:
            rows = self._rows(
                "MATCH (a:Entity)-[e:RELATED_TO]->(b:Entity) "
                "WHERE a.name = $n RETURN b.name",
                {"n": name},
            )
            out.update(r[0] for r in rows)
            rows = self._rows(
                "MATCH (a:Entity)-[e:RELATED_TO]->(b:Entity) "
                "WHERE b.name = $n RETURN a.name",
                {"n": name},
            )
            out.update(r[0] for r in rows)
        return sorted(out)

    # -- 엔티티 병합 (야간 통합용) -------------------------------------------

    def merge_entities(self, keep_id: str, drop_id: str) -> bool:
        """drop 엔티티를 keep에 병합 (별칭 합집합 + OBSERVED_IN 이관)."""
        keep = self.get_node(ENTITY, keep_id)
        drop = self.get_node(ENTITY, drop_id)
        if keep is None or drop is None or keep_id == drop_id:
            return False
        merged = sorted(
            set(schema.parse_json_list(keep["aliases"]))
            | set(schema.parse_json_list(drop["aliases"]))
            | {drop["name"].strip()}
        )
        if keep["name"].strip() in merged:
            merged.remove(keep["name"].strip())
        desc = self._accumulate_description(keep.get("description") or "",
                                            drop.get("description") or "")
        self._conn.execute(
            "MATCH (e:Entity {id: $id}) "
            "SET e.aliases = $aliases, e.description = $descr",
            {"id": keep_id, "aliases": schema.json_list(merged), "descr": desc},
        )
        rows = self._rows(
            "MATCH (d:Entity {id: $drop})-[e:OBSERVED_IN]->(o:Observation) "
            "RETURN o.id, e.evidence, e.valid_from, e.valid_to, e.recorded_at",
            {"drop": drop_id},
        )
        for obs_id, evidence, vf, vt, rec in rows:
            dup = self._one(
                "MATCH (k:Entity {id: $keep})-[e:OBSERVED_IN]->"
                "(o:Observation {id: $obs}) RETURN e.evidence",
                {"keep": keep_id, "obs": obs_id},
            )
            if dup is None:
                self._conn.execute(
                    "MATCH (k:Entity {id: $keep}) "
                    "MATCH (o:Observation {id: $obs}) "
                    "CREATE (k)-[e:OBSERVED_IN {evidence: $ev, valid_from: $vf, "
                    "valid_to: $vt, recorded_at: $rec}]->(o)",
                    {"keep": keep_id, "obs": obs_id, "ev": evidence,
                     "vf": vf, "vt": vt, "rec": rec},
                )
        self.delete_node(ENTITY, drop_id)
        return True

    # -- 예측 적중 기록 (주간 점검용) ------------------------------------------

    def log_prediction(self, hyp_id: str, episode_id: str,
                       predicted: str, actual: str) -> str:
        pid = schema.new_id("pred")
        self._conn.execute(
            "CREATE (p:Prediction {id: $id, hyp_id: $h, episode_id: $ep, "
            "predicted: $pr, actual: $ac, hit: $hit, recorded_at: $rec})",
            {"id": pid, "h": hyp_id, "ep": episode_id,
             "pr": predicted, "ac": actual,
             "hit": predicted == actual, "rec": schema.utcnow_iso()},
        )
        return pid

    def hit_stats(self, hyp_id: str) -> dict:
        rows = self._rows("MATCH (p:Prediction {hyp_id: $h}) RETURN p.hit",
                          {"h": hyp_id})
        hits = sum(1 for r in rows if r[0])
        return {"n": len(rows), "hits": hits,
                "rate": (hits / len(rows)) if rows else None}

    # -- 이해 동일시 (반복 관찰의 근거 누적용) ----------------------------------

    def find_hypothesis_by_statement(self, statement: str) -> str | None:
        rows = self._rows("MATCH (h:Hypothesis) RETURN h.id, h.statement")
        want = self.signature(statement)
        for hid, stmt in rows:
            if self.signature(stmt or "") == want:
                return hid
        return None

    def find_preference(self, target: str, direction: str) -> str | None:
        rows = self._rows(
            "MATCH (p:Preference {direction: $d}) RETURN p.id, p.target",
            {"d": direction},
        )
        want = self.signature(target)
        for pid, tgt in rows:
            if self.signature(tgt or "") == want:
                return pid
        return None

    def find_criterion(self, statement: str) -> str | None:
        rows = self._rows("MATCH (c:Criterion) RETURN c.id, c.statement")
        want = self.signature(statement)
        for cid, stmt in rows:
            if self.signature(stmt or "") == want:
                return cid
        return None

    def find_style(self, pattern: str, intent: str) -> str | None:
        row = self._one(
            "MATCH (s:Style {pattern: $p, intent: $i}) RETURN s.id",
            {"p": pattern, "i": intent},
        )
        return row[0] if row else None

    def find_context(self, kind: str, value: str) -> str | None:
        row = self._one(
            "MATCH (c:Context {kind: $k, value: $v}) RETURN c.id",
            {"k": kind, "v": value},
        )
        return row[0] if row else None

    # -- 개입 이력 (빈도 상한용) ------------------------------------------------

    def log_intervention(self, topic: str, level: int,
                         recorded_at: str | None = None) -> str:
        iid = schema.new_id("int")
        self._conn.execute(
            "CREATE (i:InterventionLog {id: $id, topic: $t, level: $lv, "
            "recorded_at: $rec})",
            {"id": iid, "t": topic, "lv": level,
             "rec": recorded_at or schema.utcnow_iso()},
        )
        return iid

    def recent_interventions(self, topic: str, since: str) -> list[dict]:
        rows = self._rows(
            "MATCH (i:InterventionLog {topic: $t}) "
            "WHERE i.recorded_at >= $since RETURN i.level, i.recorded_at",
            {"t": topic, "since": since},
        )
        return [{"level": r[0], "recorded_at": r[1]} for r in rows]

    # -- 상위 조회 --------------------------------------------------------------
    # 저장소 밖에서 쿼리 언어를 직접 쓰지 않도록 모은 조회들. 반환은 행(list/tuple)
    # 형태로 두어 호출부가 그대로 풀어 쓸 수 있다. Postgres 구현과 같은 계약이다.

    def count_nodes(self, kind: str) -> int:
        if kind not in schema.NODE_TYPES:
            raise SchemaError(f"unknown node type: {kind}")
        row = self._one(f"MATCH (n:{kind}) RETURN count(*)")
        return int(row[0]) if row else 0

    def node_ids(self, kind: str) -> list[str]:
        if kind not in schema.NODE_TYPES:
            raise SchemaError(f"unknown node type: {kind}")
        return [r[0] for r in self._rows(f"MATCH (n:{kind}) RETURN n.id")]

    def edge_rows(self, rel: str, from_kind: str, to_kind: str) -> list[list]:
        """(from_id, to_id, evidence) for one allowed (rel, from_kind, to_kind)."""
        if (from_kind, to_kind) not in schema.EDGE_ENDPOINTS.get(rel, frozenset()):
            raise SchemaError(f"{rel} cannot connect {from_kind} -> {to_kind}")
        return self._rows(
            f"MATCH (a:{from_kind})-[e:{rel}]->(b:{to_kind}) "
            "RETURN a.id, b.id, e.evidence")

    def observation_rows(self) -> list[list]:
        """(id, source_id, occurred_at, text), oldest first."""
        return self._rows(
            "MATCH (o:Observation) RETURN o.id, o.source_id, o.occurred_at, o.text "
            "ORDER BY o.occurred_at")

    def observation_texts(self) -> list[list]:
        """(text, occurred_at), oldest first."""
        return self._rows(
            "MATCH (o:Observation) RETURN o.text, o.occurred_at "
            "ORDER BY o.occurred_at")

    def recent_observations(self, limit: int) -> list[list]:
        """(id, text, occurred_at, source_id), newest recorded first."""
        return self._rows(
            "MATCH (o:Observation) RETURN o.id, o.text, o.occurred_at, "
            "o.source_id ORDER BY o.recorded_at DESC LIMIT $n", {"n": limit})

    def observation_links(self, obs_id: str) -> dict:
        """Entity names, context values and hypothesis statements tied to one observation."""
        return {
            "entities": [r[0] for r in self._rows(
                "MATCH (e:Entity)-[:OBSERVED_IN]->(o:Observation {id: $i}) "
                "RETURN e.name", {"i": obs_id})],
            "contexts": [r[0] for r in self._rows(
                "MATCH (c:Context)-[:OBSERVED_IN]->(o:Observation {id: $i}) "
                "RETURN c.value", {"i": obs_id})],
            "hypotheses": [r[0] for r in self._rows(
                "MATCH (o:Observation {id: $i})-[:SUPPORTS]->(h:Hypothesis) "
                "RETURN h.statement", {"i": obs_id})],
        }

    def hypothesis_statements(self) -> list[str]:
        return [r[0] for r in self._rows("MATCH (h:Hypothesis) RETURN h.statement")]

    def hypothesis_catalog(self) -> list[dict]:
        """Candidate and active hypotheses with ids, for the extractor to link evidence to."""
        rows = self._rows(
            "MATCH (h:Hypothesis) WHERE h.status IN ['candidate', 'active'] "
            "RETURN h.id, h.statement, h.status, h.conditions ORDER BY h.recorded_at")
        return [{"id": r[0], "statement": r[1], "status": r[2],
                 "conditions": schema.parse_json_list(r[3])} for r in rows]

    def hypothesis_brief(self, limit: int) -> list[list]:
        """(statement, status) for the first `limit` hypotheses."""
        return self._rows(
            "MATCH (h:Hypothesis) RETURN h.statement, h.status LIMIT $n", {"n": limit})

    def hypothesis_ids_by_status(self, statuses: list[str], as_of: str) -> list[str]:
        return [r[0] for r in self._rows(
            "MATCH (h:Hypothesis) WHERE h.status IN $st AND h.recorded_at <= $as_of "
            "RETURN h.id", {"st": list(statuses), "as_of": as_of})]

    def hypotheses_recorded_between(self, since: str, as_of: str) -> list[list]:
        """(id, statement, status, use_policy, domain)."""
        return self._rows(
            "MATCH (h:Hypothesis) WHERE h.recorded_at >= $since "
            "AND h.recorded_at <= $as_of "
            "RETURN h.id, h.statement, h.status, h.use_policy, h.domain",
            {"since": since, "as_of": as_of})

    def hypotheses_changed_between(self, since: str, as_of: str) -> list[list]:
        """(id, statement, status) whose status changed in the window."""
        return self._rows(
            "MATCH (h:Hypothesis) WHERE h.status_changed_at >= $since "
            "AND h.status_changed_at <= $as_of RETURN h.id, h.statement, h.status",
            {"since": since, "as_of": as_of})

    def corrections_between(self, since: str, as_of: str) -> list[list]:
        """(id, kind, target, text)."""
        return self._rows(
            "MATCH (c:Correction) WHERE c.recorded_at >= $since "
            "AND c.recorded_at <= $as_of RETURN c.id, c.kind, c.target, c.text",
            {"since": since, "as_of": as_of})

    def entity_observation_counts(self) -> list[list]:
        """(name, kind, number of observations)."""
        return self._rows(
            "MATCH (e:Entity)-[:OBSERVED_IN]->(o:Observation) "
            "RETURN e.name, e.kind, count(o)")

    def context_observation_counts(self) -> list[list]:
        """(kind, value, number of observations)."""
        return self._rows(
            "MATCH (c:Context)-[:OBSERVED_IN]->(o:Observation) "
            "RETURN c.kind, c.value, count(o)")

    def preference_rows(self) -> list[list]:
        return self._rows(
            "MATCH (p:Preference) RETURN p.target, p.direction, p.strength_evidence")

    def criterion_rows(self) -> list[list]:
        return self._rows("MATCH (c:Criterion) RETURN c.statement, c.applies_to")

    def style_rows(self) -> list[list]:
        """(pattern, intent, example_refs)."""
        return self._rows("MATCH (s:Style) RETURN s.pattern, s.intent, s.example_refs")

    def style_brief(self) -> list[list]:
        """(id, pattern, intent)."""
        return self._rows("MATCH (s:Style) RETURN s.id, s.pattern, s.intent")

    def styles_by_intent(self, intent: str) -> list[list]:
        """(id, example_refs)."""
        return self._rows(
            "MATCH (s:Style {intent: $i}) RETURN s.id, s.example_refs", {"i": intent})

    def style_ids_by_pattern(self, pattern: str) -> list[str]:
        return [r[0] for r in self._rows(
            "MATCH (s:Style {pattern: $p}) RETURN s.id", {"p": pattern})]

    def entity_aliases(self) -> list[list]:
        """(name, aliases-json)."""
        return self._rows("MATCH (e:Entity) RETURN e.name, e.aliases")

    def entity_ids(self) -> list[list]:
        """(id, kind, name, recorded_at)."""
        return self._rows("MATCH (e:Entity) RETURN e.id, e.kind, e.name, e.recorded_at")

    def entity_brief(self, limit: int) -> list[list]:
        """(name, kind, description)."""
        return self._rows(
            "MATCH (e:Entity) RETURN e.name, e.kind, e.description LIMIT $n",
            {"n": limit})

    def cosupported_pairs(self) -> list[list]:
        """Hypothesis pairs supported by one observation: (h1, h2, obs, status1, status2)."""
        return self._rows(
            "MATCH (o:Observation)-[e:SUPPORTS]->(h1:Hypothesis) "
            "MATCH (o)-[e2:SUPPORTS]->(h2:Hypothesis) "
            "WHERE h1.id < h2.id RETURN h1.id, h2.id, o.id, h1.status, h2.status")

    def has_alternative(self, a: str, b: str) -> bool:
        return self._one(
            "MATCH (x:Hypothesis {id: $a})-[e:ALTERNATIVE_TO]->"
            "(y:Hypothesis {id: $b}) RETURN e.evidence", {"a": a, "b": b}) is not None
