-- Background jobs (learning from a new memory takes 10-40 s, so it runs off the request).
--
-- A signed-in user can queue a job and read their own; they cannot change a job's status
-- (no UPDATE grant). Like api_tokens, RLS is enabled but not forced, so the server's own
-- role (the table owner) claims and completes jobs across users.

create table public.jobs (
  user_id     uuid not null default auth.uid() references auth.users (id) on delete cascade,
  id          text not null,
  kind        text not null,
  payload     text not null default '{}',
  status      text not null default 'queued',
  attempts    integer not null default 0,
  run_after   text not null,
  created_at  text not null,
  started_at  text,
  finished_at text,
  result      text,
  error       text,
  primary key (user_id, id)
);
create index jobs_claim_idx on public.jobs (status, run_after);
create index jobs_user_idx  on public.jobs (user_id, created_at);

alter table public.jobs enable row level security;
create policy jobs_owner on public.jobs for all to authenticated
  using (user_id = (select auth.uid())) with check (user_id = (select auth.uid()));
revoke all on public.jobs from anon;
grant select, insert, delete on public.jobs to authenticated;
