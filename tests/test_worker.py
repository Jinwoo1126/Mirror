"""Background learning: queueing over HTTP, one-run-per-job, per-user ordering, retries."""

import threading
import time

import psycopg
import pytest
from starlette.testclient import TestClient

from mirror import app as app_mod
from mirror import extract as ex
from mirror import worker
from tests.conftest import DINNER_TEXT, PG_DSN, drop_pg_user, new_pg_user
from tests.test_hosted import SECRET, make_token

pytestmark = pytest.mark.skipif(not PG_DSN, reason="set MIRROR_TEST_PG_DSN")


class Boom:
    """An extractor that always fails the way a flaky LLM does."""
    def extract(self, obs):
        raise ex.ExtractionError("model unavailable")


def sql(q, params=()):
    with psycopg.connect(PG_DSN, autocommit=True) as c:
        cur = c.execute(q, params)
        return cur.fetchall() if cur.description else None


@pytest.fixture
def env(monkeypatch):
    monkeypatch.setenv("MIRROR_HOSTED", "1")
    monkeypatch.setenv("SUPABASE_JWT_SECRET", SECRET)
    monkeypatch.setenv("MIRROR_PG_DSN", PG_DSN)
    monkeypatch.setenv("MIRROR_NO_INDEX", "1")
    monkeypatch.setenv("MIRROR_WORKER", "0")        # tests drive the worker by hand
    monkeypatch.setenv("MIRROR_RATE_COSTLY", "1000")   # the request limit has its own test
    monkeypatch.setenv("MIRROR_RATE_ALL", "1000")
    app_mod._limiter.hits.clear()
    users = [new_pg_user(PG_DSN) for _ in range(3)]
    sql("delete from public.jobs")
    yield TestClient(app_mod.app), users, {u: {"Authorization": "Bearer " + make_token(u)} for u in users}
    for u in users:
        drop_pg_user(PG_DSN, u)


def add(c, h, text=DINNER_TEXT):
    return c.post("/api/memories", headers=h, json={"text": text})


def drain(extractor):
    n = 0
    while worker.run_one(PG_DSN, extractor):
        n += 1
    return n


def test_saving_returns_at_once_and_the_worker_does_the_learning(env):
    c, (a, b, _), h = env
    r = add(c, h[a])
    assert r.status_code == 202 and r.json()["status"] == "queued"
    job = r.json()["job_id"]
    assert c.get("/api/status", headers=h[a]).json()["counts"]["Observation"] == 0   # not yet
    assert c.get(f"/api/jobs/{job}", headers=h[a]).json()["status"] == "queued"

    assert drain(ex.MockExtractor()) == 1
    done = c.get(f"/api/jobs/{job}", headers=h[a]).json()
    assert done["status"] == "done" and done["result"]["ok"] is True
    assert c.get("/api/status", headers=h[a]).json()["counts"]["Observation"] == 1
    assert c.get("/api/status", headers=h[b]).json()["counts"]["Observation"] == 0   # not B's


def test_another_user_cannot_see_the_job(env):
    c, (a, b, _), h = env
    job = add(c, h[a]).json()["job_id"]
    assert c.get(f"/api/jobs/{job}", headers=h[b]).status_code == 404


def test_a_personal_token_can_save_and_poll(env):
    c, (a, *_), h = env
    raw = c.post("/api/tokens", headers=h[a], json={"name": "hook"}).json()["token"]
    t = {"Authorization": "Bearer " + raw}
    job = add(c, t).json()["job_id"]
    assert c.get(f"/api/jobs/{job}", headers=t).json()["status"] == "queued"


def test_repeating_a_source_id_does_not_queue_twice(env):
    c, (a, *_), h = env
    first = c.post("/api/memories", headers=h[a], json={"text": DINNER_TEXT, "source_id": "x1"})
    drain(ex.MockExtractor())
    again = c.post("/api/memories", headers=h[a], json={"text": DINNER_TEXT, "source_id": "x1"})
    assert first.status_code == 202 and again.json()["duplicate"] is True
    assert sql("select count(*) from public.jobs where user_id=%s", (a,))[0][0] == 1


def test_a_failing_extractor_is_retried_then_given_up_on(env):
    c, (a, *_), h = env
    job = add(c, h[a]).json()["job_id"]
    for attempt in (1, 2):
        assert drain(Boom()) == 1
        j = c.get(f"/api/jobs/{job}", headers=h[a]).json()
        assert j["status"] == "queued" and j["attempts"] == attempt and "unavailable" in j["error"]
        assert drain(Boom()) == 0                      # not due yet: backs off
        sql("update public.jobs set run_after='2000-01-01T00:00:00+00:00'")
    assert drain(Boom()) == 1
    j = c.get(f"/api/jobs/{job}", headers=h[a]).json()
    assert j["status"] == "failed" and j["attempts"] == worker.MAX_ATTEMPTS
    assert c.get("/api/status", headers=h[a]).json()["counts"]["Observation"] == 0


def test_a_retry_that_succeeds_ends_done(env):
    c, (a, *_), h = env
    job = add(c, h[a]).json()["job_id"]
    drain(Boom())
    sql("update public.jobs set run_after='2000-01-01T00:00:00+00:00'")
    drain(ex.MockExtractor())
    j = c.get(f"/api/jobs/{job}", headers=h[a]).json()
    assert j["status"] == "done" and j["attempts"] == 2


def test_empty_text_fails_without_retrying(env):
    c, (a, *_), h = env
    sql_job = c.post("/api/memories", headers=h[a], json={"text": "x" * 3}).json()["job_id"]
    sql("update public.jobs set payload='{\"text\": \"\", \"source_id\": \"e\"}'")
    drain(ex.MockExtractor())
    j = c.get(f"/api/jobs/{sql_job}", headers=h[a]).json()
    assert j["status"] == "failed" and j["attempts"] == 1


def test_stuck_running_jobs_are_put_back_and_eventually_failed(env):
    c, (a, *_), h = env
    job = add(c, h[a]).json()["job_id"]
    assert worker.claim(PG_DSN)["id"] == job                      # a worker takes it, then dies
    assert worker.recover_stale(PG_DSN) == 0                      # still fresh
    sql("update public.jobs set started_at='2000-01-01T00:00:00+00:00'")
    assert worker.recover_stale(PG_DSN) == 1
    assert c.get(f"/api/jobs/{job}", headers=h[a]).json()["status"] == "queued"
    sql("update public.jobs set status='running', attempts=3, started_at='2000-01-01T00:00:00+00:00'")
    worker.recover_stale(PG_DSN)
    assert c.get(f"/api/jobs/{job}", headers=h[a]).json()["status"] == "failed"


def test_the_queue_has_a_per_user_limit(env):
    c, (a, b, _), h = env
    for i in range(20):
        assert add(c, h[a], f"{DINNER_TEXT} {i}").status_code == 202
    over = add(c, h[a], "one too many please")
    assert over.status_code == 429
    assert add(c, h[b]).status_code == 202                        # B is unaffected


def test_many_workers_run_each_job_exactly_once_and_one_user_at_a_time(env):
    c, (a, b, d), h = env
    for u in (a, b, d):
        for i in range(4):
            assert add(c, h[u], f"{DINNER_TEXT} variant {i}").status_code == 202
    errors = []

    def work():
        extractor = ex.MockExtractor()
        try:
            while worker.run_one(PG_DSN, extractor):
                pass
        except Exception as e:                      # pragma: no cover
            errors.append(e)

    threads = [threading.Thread(target=work) for _ in range(4)]
    [t.start() for t in threads]
    [t.join(timeout=60) for t in threads]
    assert not errors
    rows = sql("select status, attempts from public.jobs")
    assert len(rows) == 12
    assert all(r == ("done", 1) for r in rows), rows          # none failed on a stale revision
    for u in (a, b, d):
        assert c.get("/api/status", headers=h[u]).json()["counts"]["Observation"] == 4


def test_deleting_the_account_data_clears_queued_work(env):
    c, (a, *_), h = env
    add(c, h[a])
    assert c.delete("/api/account", headers=h[a]).status_code == 200
    assert sql("select count(*) from public.jobs where user_id=%s", (a,))[0][0] == 0


def test_lock_key_is_stable_and_fits_a_bigint():
    k = worker._lock_key("11111111-1111-1111-1111-111111111111")
    assert k == worker._lock_key("11111111-1111-1111-1111-111111111111")
    assert 0 <= k < 2**63


def test_sample_memories_load_once_into_an_empty_account(env):
    c, (a, b, _), h = env
    r = c.post("/api/sample", headers=h[a])
    assert r.status_code == 202 and len(r.json()["job_ids"]) == 8
    assert drain(ex.MockExtractor()) == 8
    assert c.get("/api/status", headers=h[a]).json()["counts"]["Observation"] == 8
    assert c.post("/api/sample", headers=h[a]).status_code == 409        # no longer empty
    assert c.get("/api/status", headers=h[b]).json()["counts"]["Observation"] == 0   # not B's


def test_sample_loading_uses_the_shared_anonymous_budget(env, monkeypatch):
    from tests.test_hosted import anon_token
    c, (a, b, _), h = env
    monkeypatch.setenv("MIRROR_ANON_COSTLY_PER_HOUR", "10")
    first = c.post("/api/sample", headers={"Authorization": "Bearer " + anon_token(a)})
    second = c.post("/api/sample", headers={"Authorization": "Bearer " + anon_token(b)})
    assert first.status_code == 202                  # 8 of the 10
    assert second.status_code == 429                 # 8 more would exceed it
    assert c.get("/api/status", headers=h[b]).json()["counts"]["Observation"] == 0


def test_samples_are_labelled_as_not_the_users_own():
    from mirror import app as app_mod
    html = (app_mod.STATIC_DIR / "app.html").read_text(encoding="utf-8")
    assert "They are not yours" in html and "Load example memories" in html
