"""Background worker: learns from new memories off the request path.

Jobs live in the `jobs` table. Any number of workers (one thread per app process, or
`python -m mirror.worker`) can run at once:

- A job is claimed with FOR UPDATE SKIP LOCKED, so two workers never take the same one.
- Jobs for one user run one at a time (a per-user advisory lock), because learning from a
  memory reads and then bumps that user's revision.
- A failed job is retried up to MAX_ATTEMPTS times with a growing delay. A job whose
  worker died mid-run is put back after STALE_AFTER seconds.

The worker runs on the server's own database role to find work across users, then does
each user's work through a PostgresStore, so row level security still applies to it.
"""

from __future__ import annotations

import hashlib
import json
import os
import threading
import time
from datetime import datetime, timedelta, timezone

from . import extract as ex
from . import observe as observe_mod
from . import schema

MAX_ATTEMPTS = 3
STALE_AFTER = 600            # seconds a job may stay "running" before it is retried
RETRY_DELAY = 30             # seconds, multiplied by the attempt number


def _iso(offset_seconds: float = 0) -> str:
    return (datetime.now(timezone.utc) + timedelta(seconds=offset_seconds)
            ).replace(microsecond=0).isoformat()


def _lock_key(user_id: str) -> int:
    """A stable 63-bit key for pg_advisory_lock."""
    return int.from_bytes(hashlib.sha256(user_id.encode()).digest()[:8], "big") >> 1


def recover_stale(dsn: str) -> int:
    """Put back jobs whose worker vanished; give up on ones that keep failing."""
    import psycopg

    cutoff = _iso(-STALE_AFTER)
    with psycopg.connect(dsn, autocommit=True) as c:
        back = c.execute(
            "update public.jobs set status='queued', run_after=%s where status='running' "
            "and started_at < %s and attempts < %s", (_iso(), cutoff, MAX_ATTEMPTS)).rowcount
        c.execute(
            "update public.jobs set status='failed', finished_at=%s, "
            "error='worker stopped before finishing' where status='running' "
            "and started_at < %s and attempts >= %s", (_iso(), cutoff, MAX_ATTEMPTS))
    return back


def claim(dsn: str) -> dict | None:
    """Take the oldest job that is due, or None."""
    import psycopg

    now = _iso()
    with psycopg.connect(dsn, autocommit=True) as c:
        row = c.execute(
            "update public.jobs set status='running', started_at=%s, attempts=attempts+1 "
            "where (user_id, id) = (select user_id, id from public.jobs "
            "  where status='queued' and run_after <= %s order by created_at "
            "  limit 1 for update skip locked) "
            "returning user_id, id, kind, payload, attempts", (now, now)).fetchone()
    if row is None:
        return None
    return {"user_id": str(row[0]), "id": row[1], "kind": row[2],
            "payload": json.loads(row[3] or "{}"), "attempts": row[4]}


def _finish(dsn: str, job: dict, status: str, result: dict | None = None,
            error: str = "", retry: bool = False) -> None:
    import psycopg

    with psycopg.connect(dsn, autocommit=True) as c:
        if retry:
            c.execute("update public.jobs set status='queued', run_after=%s, error=%s "
                      "where user_id=%s and id=%s",
                      (_iso(RETRY_DELAY * job["attempts"]), error[:500],
                       job["user_id"], job["id"]))
        else:
            c.execute("update public.jobs set status=%s, finished_at=%s, result=%s, error=%s "
                      "where user_id=%s and id=%s",
                      (status, _iso(), json.dumps(result) if result is not None else None,
                       error[:500] or None, job["user_id"], job["id"]))


def _learn(dsn: str, job: dict, extractor) -> tuple[bool, dict, str, bool]:
    """Run one 'learn from this text' job. Returns (ok, result, error, retryable)."""
    from .pg_store import PostgresStore

    p = job["payload"]
    store = PostgresStore(dsn, job["user_id"])
    try:
        res = observe_mod.observe(
            store, extractor, p.get("source_id", ""),
            {"text": p.get("text", ""), "occurred_at": p.get("occurred_at")},
            store.get_revision())
    finally:
        store.close()
    out = schema.to_jsonable(res)
    # A validation or empty-text failure will fail the same way again. Extractor and
    # database trouble may not.
    retryable = (not res.ok) and (res.error.startswith("extract:")
                                  or res.error.startswith("stale_revision"))
    return res.ok, out, res.error, retryable


def run_one(dsn: str, extractor) -> bool:
    """Claim and run one job. Returns False when there was nothing to do."""
    import psycopg

    job = claim(dsn)
    if job is None:
        return False
    # One job per user at a time. The lock lives on this connection and is released
    # when it closes, even if this process dies.
    lock = psycopg.connect(dsn, autocommit=True)
    try:
        lock.execute("select pg_advisory_lock(%s)", (_lock_key(job["user_id"]),))
        if job["kind"] != "learn":
            _finish(dsn, job, "failed", error=f"unknown job kind: {job['kind']}")
            return True
        try:
            ok, result, error, retryable = _learn(dsn, job, extractor)
        except Exception as e:                      # database or extractor crash
            ok, result, error, retryable = False, {}, f"{type(e).__name__}: {e}", True
        if ok:
            _finish(dsn, job, "done", result)
        elif retryable and job["attempts"] < MAX_ATTEMPTS:
            _finish(dsn, job, "queued", error=error, retry=True)
        else:
            _finish(dsn, job, "failed", result, error)
    finally:
        lock.close()
    return True


def loop(dsn: str, stop: threading.Event, poll: float = 2.0, extractor=None) -> None:
    extractor = extractor or ex.get_extractor(os.environ.get("MIRROR_EXTRACTOR", "spark"))
    last_sweep = 0.0
    while not stop.is_set():
        try:
            if time.monotonic() - last_sweep > 60:
                recover_stale(dsn)
                last_sweep = time.monotonic()
            if not run_one(dsn, extractor):
                stop.wait(poll)
        except Exception:
            stop.wait(poll * 2)          # database blip: back off, keep the thread alive


def start_thread(dsn: str) -> tuple[threading.Thread, threading.Event]:
    stop = threading.Event()
    t = threading.Thread(target=loop, args=(dsn, stop), name="mirror-worker", daemon=True)
    t.start()
    return t, stop


def main() -> None:
    dsn = os.environ.get("MIRROR_PG_DSN", "")
    if not dsn:
        raise SystemExit("MIRROR_PG_DSN is required")
    stop = threading.Event()
    try:
        loop(dsn, stop)
    except KeyboardInterrupt:
        stop.set()


if __name__ == "__main__":
    main()
