-- Mirror: knowledge graph storage on Postgres (Supabase).
--
-- One row set per user. Every table carries user_id, references auth.users with
-- ON DELETE CASCADE (deleting an account deletes all of its data), and has
-- row level security so a user can only ever see their own rows, even if the API
-- layer forgets a WHERE clause.
--
-- Timestamps are ISO-8601 text on purpose. The graph stores 'unknown' for
-- observations without a date, and bi-temporal replay compares ISO strings; this
-- matches the embedded Kuzu store exactly.

create table public.observations (
  user_id     uuid not null default auth.uid() references auth.users (id) on delete cascade,
  seq         bigint generated always as identity,
  id          text not null,
  source_id   text not null,
  text        text not null,
  occurred_at text not null,
  raw_ref     text not null default '',
  visibility  text not null default 'private',
  recorded_at text not null,
  primary key (user_id, id),
  unique (user_id, source_id)
);

create table public.entities (
  user_id     uuid not null default auth.uid() references auth.users (id) on delete cascade,
  seq         bigint generated always as identity,
  id          text not null,
  kind        text not null,
  name        text not null,
  aliases     text not null default '[]',
  recorded_at text not null,
  description text not null default '',
  primary key (user_id, id)
);

create table public.contexts (
  user_id     uuid not null default auth.uid() references auth.users (id) on delete cascade,
  seq         bigint generated always as identity,
  id          text not null,
  kind        text not null,
  value       text not null,
  valid_from  text,
  valid_to    text,
  recorded_at text not null,
  primary key (user_id, id)
);

create table public.preferences (
  user_id           uuid not null default auth.uid() references auth.users (id) on delete cascade,
  seq         bigint generated always as identity,
  id                text not null,
  target            text not null,
  direction         text not null,
  strength_evidence text not null default '',
  valid_from        text,
  valid_to          text,
  recorded_at       text not null,
  primary key (user_id, id)
);

create table public.criteria (
  user_id     uuid not null default auth.uid() references auth.users (id) on delete cascade,
  seq         bigint generated always as identity,
  id          text not null,
  statement   text not null,
  applies_to  text not null default '',
  recorded_at text not null,
  primary key (user_id, id)
);

create table public.hypotheses (
  user_id           uuid not null default auth.uid() references auth.users (id) on delete cascade,
  seq         bigint generated always as identity,
  id                text not null,
  conditions        text not null default '[]',
  statement         text not null,
  status            text not null,
  use_policy        text not null,
  domain            text not null default '',
  valid_from        text,
  valid_to          text,
  recorded_at       text not null,
  status_changed_at text not null default '',
  primary key (user_id, id)
);

create table public.styles (
  user_id      uuid not null default auth.uid() references auth.users (id) on delete cascade,
  seq         bigint generated always as identity,
  id           text not null,
  pattern      text not null,
  example_refs text not null default '[]',
  intent       text not null default '',
  recorded_at  text not null,
  primary key (user_id, id)
);

create table public.corrections (
  user_id     uuid not null default auth.uid() references auth.users (id) on delete cascade,
  seq         bigint generated always as identity,
  id          text not null,
  kind        text not null,
  target      text not null,
  text        text not null default '',
  recorded_at text not null,
  primary key (user_id, id)
);

create table public.responses (
  user_id    uuid not null default auth.uid() references auth.users (id) on delete cascade,
  seq         bigint generated always as identity,
  id         text not null,
  text       text not null default '',
  created_at text not null,
  primary key (user_id, id)
);

-- Edges between nodes. The allowed (rel, from_kind, to_kind) combinations and the
-- "evidence is an Observation" rule are enforced by the application
-- (mirror/schema.py); the database enforces tenancy and referential cleanup.
create table public.edges (
  user_id     uuid not null default auth.uid() references auth.users (id) on delete cascade,
  seq         bigint generated always as identity,
  rel         text not null,
  from_kind   text not null,
  from_id     text not null,
  to_kind     text not null,
  to_id       text not null,
  evidence    text not null default '',
  phrase      text not null default '',
  valid_from  text,
  valid_to    text,
  recorded_at text not null,
  primary key (user_id, seq)
);
create index edges_to_idx   on public.edges (user_id, rel, to_kind, to_id);
create index edges_from_idx on public.edges (user_id, rel, from_kind, from_id);

create table public.meta (
  user_id uuid not null default auth.uid() references auth.users (id) on delete cascade,
  key     text not null,
  value   text not null,
  primary key (user_id, key)
);

create table public.blocked_understandings (
  user_id     uuid not null default auth.uid() references auth.users (id) on delete cascade,
  seq         bigint generated always as identity,
  id          text not null,
  signature   text not null,
  reason      text not null default '',
  recorded_at text not null,
  primary key (user_id, id)
);

create table public.predictions (
  user_id     uuid not null default auth.uid() references auth.users (id) on delete cascade,
  seq         bigint generated always as identity,
  id          text not null,
  hyp_id      text not null,
  episode_id  text not null,
  predicted   text not null,
  actual      text not null,
  hit         boolean not null,
  recorded_at text not null,
  primary key (user_id, id)
);

create table public.intervention_logs (
  user_id     uuid not null default auth.uid() references auth.users (id) on delete cascade,
  seq         bigint generated always as identity,
  id          text not null,
  topic       text not null,
  level       integer not null,
  recorded_at text not null,
  primary key (user_id, id)
);

create index observations_recorded_idx on public.observations (user_id, recorded_at);
create index hypotheses_status_idx     on public.hypotheses (user_id, status);
create index entities_kind_idx         on public.entities (user_id, kind);
create index predictions_hyp_idx       on public.predictions (user_id, hyp_id);
create index interventions_topic_idx   on public.intervention_logs (user_id, topic);

-- Row level security: a signed-in user reaches only rows whose user_id is theirs.
do $$
declare t text;
begin
  foreach t in array array[
    'observations', 'entities', 'contexts', 'preferences', 'criteria',
    'hypotheses', 'styles', 'corrections', 'responses', 'edges', 'meta',
    'blocked_understandings', 'predictions', 'intervention_logs'
  ] loop
    execute format('alter table public.%I enable row level security', t);
    execute format('alter table public.%I force row level security', t);
    execute format(
      'create policy %I on public.%I for all to authenticated '
      'using (user_id = (select auth.uid())) with check (user_id = (select auth.uid()))',
      t || '_owner', t);
    -- anonymous visitors get nothing, not even through the Data API
    execute format('revoke all on public.%I from anon', t);
    execute format('grant select, insert, update, delete on public.%I to authenticated', t);
  end loop;
end $$;
