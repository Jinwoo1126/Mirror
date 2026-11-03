# Mirror on Supabase

`migrations/` is the schema for the hosted service: one set of tables per user, row
level security on every table, and `ON DELETE CASCADE` from `auth.users` so deleting an
account deletes its data. `mirror/pg_store.py` is the matching store.

## Run the tests against a local Postgres

Plain Postgres is enough. `local/auth_stub.sql` stands in for the parts of Supabase the
migration uses (`auth.users`, `auth.uid()`, the `anon` and `authenticated` roles).

```sh
docker run -d --name mirror-pg-test -e POSTGRES_PASSWORD=mirror-test-only \
  -e POSTGRES_DB=mirror -p 127.0.0.1:54329:5432 postgres:16
docker exec -i mirror-pg-test psql -U postgres -d mirror -v ON_ERROR_STOP=1 < supabase/local/auth_stub.sql
docker exec -i mirror-pg-test psql -U postgres -d mirror -v ON_ERROR_STOP=1 < supabase/migrations/20260101000000_init.sql

uv pip install --python .venv/bin/python -e ".[hosted]"
MIRROR_TEST_PG_DSN=postgresql://postgres:mirror-test-only@127.0.0.1:54329/mirror \
  .venv/bin/python -m pytest
```

Without `MIRROR_TEST_PG_DSN` the Postgres variants are skipped and only Kuzu runs.

## How tenancy works

`PostgresStore(dsn, user_id)` runs every statement in a transaction that first does
`SET LOCAL ROLE authenticated` and sets the user id as the JWT subject, the same thing
PostgREST does. Row level security therefore applies even to the store's own queries.
On Supabase, connect with the `postgres` role (or the pooler) and pass the verified
user id from the access token.
