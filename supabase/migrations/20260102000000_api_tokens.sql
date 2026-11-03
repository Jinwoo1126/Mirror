-- Personal API tokens for the things that cannot do a browser sign-in: Claude Code and
-- Muse Code hooks, MCP clients, a Muse connector.
--
-- Only a SHA-256 hash is stored; the token itself is shown once, when it is created.
-- Tokens are long random strings, so an unsalted hash is enough.
--
-- Row level security lets a signed-in user manage only their own tokens. It is NOT
-- forced on this table, so the server's own database role (the table owner) can look a
-- token up by hash across users when a request arrives with one. The `authenticated`
-- and `anon` roles remain bound by the policy.

create table public.api_tokens (
  user_id      uuid not null default auth.uid() references auth.users (id) on delete cascade,
  id           text not null,
  name         text not null,
  prefix       text not null,
  token_hash   text not null unique,
  created_at   text not null,
  last_used_at text,
  revoked_at   text,
  primary key (user_id, id)
);
create index api_tokens_user_idx on public.api_tokens (user_id, created_at);

alter table public.api_tokens enable row level security;
create policy api_tokens_owner on public.api_tokens for all to authenticated
  using (user_id = (select auth.uid())) with check (user_id = (select auth.uid()));
revoke all on public.api_tokens from anon;
grant select, insert, update, delete on public.api_tokens to authenticated;
