"""Postgres store: tenancy (row level security) and parity with the Kuzu store.

The tenancy tests need a database; set MIRROR_TEST_PG_DSN (see conftest.py). The
parity guard needs nothing and always runs.
"""

import inspect

import pytest

from mirror import schema
from mirror.pg_store import PostgresStore
from mirror.store import MirrorStore

from tests.conftest import PG_DSN, drop_pg_user, new_pg_user

needs_pg = pytest.mark.skipif(not PG_DSN, reason="set MIRROR_TEST_PG_DSN")


def test_every_cypher_method_is_overridden():
    """A MirrorStore method that talks Cypher and is missing from PostgresStore would
    fail only at runtime, in production. Catch it here."""
    # Kuzu schema setup. Only MirrorStore.__init__ calls these; Postgres schema is
    # managed by supabase/migrations instead.
    kuzu_setup_only = {"_tables", "_ensure_schema", "_migrate", "_add_column"}
    missing = []
    for name, fn in inspect.getmembers(MirrorStore, inspect.isfunction):
        if name.startswith("__") or name in PostgresStore.__dict__:
            continue
        if name in kuzu_setup_only:
            continue
        src = inspect.getsource(fn)
        if "_rows(" in src or "_one(" in src or "_conn" in src:
            missing.append(name)
    assert missing == []


@pytest.fixture
def two_users():
    a, b = new_pg_user(PG_DSN), new_pg_user(PG_DSN)
    sa, sb = PostgresStore(PG_DSN, a), PostgresStore(PG_DSN, b)
    yield sa, sb
    sa.close()
    sb.close()
    drop_pg_user(PG_DSN, a)
    drop_pg_user(PG_DSN, b)


def _obs(i="obs-1", src="s1", text="Picked the usual ramen again."):
    return schema.Observation(id=i, source_id=src, text=text,
                              occurred_at="2026-10-16T20:00:00+09:00")


@needs_pg
def test_users_cannot_see_each_others_rows_even_without_a_filter(two_users):
    a, b = two_users
    assert a.add_observation(_obs())
    assert a.count_nodes("Observation") == 1
    # Raw SQL with no user_id filter: row level security alone must hide A's row.
    assert b._q("select count(*) from public.observations")[0][0] == 0
    assert b.get_observation("obs-1") is None
    assert b.node_ids("Observation") == []


@needs_pg
def test_same_ids_and_source_ids_do_not_collide_across_users(two_users):
    a, b = two_users
    assert a.add_observation(_obs())
    assert b.add_observation(_obs())  # same id and source_id, different owner
    assert a.count_nodes("Observation") == 1 and b.count_nodes("Observation") == 1


@needs_pg
def test_cannot_write_rows_for_another_user(two_users):
    import psycopg
    a, b = two_users
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        b._x("insert into public.observations (user_id, id, source_id, text, "
             "occurred_at, recorded_at) values (%s, 'x', 'x', 'x', 'x', 'x')", (a.uid,))
    assert a.count_nodes("Observation") == 0


@needs_pg
def test_cannot_update_or_delete_another_users_rows(two_users):
    a, b = two_users
    a.add_observation(_obs())
    assert b._x("update public.observations set text='hijacked'") == 0
    assert b._x("delete from public.observations") == 0
    assert a.get_observation("obs-1").text == "Picked the usual ramen again."


@needs_pg
def test_anonymous_role_gets_nothing(two_users):
    import psycopg
    a, _ = two_users
    a.add_observation(_obs())
    with psycopg.connect(PG_DSN, autocommit=True) as c:
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            with c.transaction():
                c.execute("set local role anon")
                c.execute("select count(*) from public.observations")


@needs_pg
def test_deleting_an_account_deletes_all_of_its_data():
    import psycopg
    uid = new_pg_user(PG_DSN)
    st = PostgresStore(PG_DSN, uid)
    st.add_observation(_obs())
    ent = st.upsert_entity("food", "ramen")
    st.add_edge(schema.Edge(rel=schema.OBSERVED_IN, from_kind=schema.ENTITY,
                            from_id=ent.id, to_kind=schema.OBSERVATION,
                            to_id="obs-1", evidence="obs-1"))
    st.bump_revision()
    st.close()
    drop_pg_user(PG_DSN, uid)
    tables = ["observations", "entities", "edges", "meta"]
    with psycopg.connect(PG_DSN, autocommit=True) as c:
        left = {t: c.execute(f"select count(*) from public.{t} where user_id = %s",
                             (uid,)).fetchone()[0] for t in tables}
    assert left == dict.fromkeys(tables, 0)


@needs_pg
def test_session_role_does_not_leak_between_statements(two_users):
    a, _ = two_users
    a.add_observation(_obs())
    # After a store call the connection is back to its own role, not `authenticated`.
    assert a._conn.execute("select current_user").fetchone()[0] == "postgres"
