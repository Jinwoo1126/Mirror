# Deploying Mirror

Two pieces: **Supabase** (Postgres, sign-in) and **one Python service** that serves the
UI and the API (Fly.io config included). Vercel is not needed.

## What you do once (accounts and keys)

1. **Supabase project** at supabase.com (Seoul region `ap-northeast-2`).
2. **Sign-in providers**: Authentication → Providers.
   - Google: create an OAuth client in Google Cloud, paste the client id and secret.
   - Meta: enable *Facebook*, paste the Meta app id and secret. The Meta app must be in
     Live mode for people other than you to sign in.
   - Authentication → URL Configuration: set Site URL to your app URL and add it to the
     redirect list.
3. **Fly.io account** and a card on file.
4. **Meta Model API key** for the LLM (`META_API_KEY`).

## Commands

```sh
# database schema
npx supabase login
npx supabase link --project-ref <ref>
npx supabase db push                    # applies supabase/migrations (init, api_tokens, jobs)

# app
brew install flyctl && fly auth login
fly launch --no-deploy --copy-config --name <your-app>
fly secrets set \
  MIRROR_PG_DSN='postgresql://postgres:<db password>@db.<ref>.supabase.co:5432/postgres' \
  SUPABASE_URL='https://<ref>.supabase.co' \
  SUPABASE_ANON_KEY='<anon key>' \
  SUPABASE_JWT_SECRET='<jwt secret, only if the project still uses the legacy secret>' \
  SUPABASE_SERVICE_ROLE_KEY='<service role key, lets "Delete my data" remove the account too>' \
  META_API_KEY='<key>' \
  MIRROR_OPERATOR_NAME='<who runs this>' MIRROR_CONTACT_EMAIL='<contact>'
fly deploy
```

Use the **session pooler** connection string if Fly cannot reach the direct host over IPv6.
The service role key is a server secret: never put it in the page or in git.

## What is enforced

- Every `/api/*` call needs a valid Supabase token; the user is taken from the token.
- Each request opens a Postgres session as the `authenticated` role with that user id, so
  row level security applies to the app's own queries (tests prove cross-user reads,
  writes and deletes are refused).
- Deleting the account removes the user's rows (`ON DELETE CASCADE`).

## Known gaps before opening it to the public

- Learning from a memory runs in a background worker (a thread in each app process, jobs in
  Postgres, retried up to 3 times, one job per user at a time). It scales with the number of
  app processes; split it into its own process (`python -m mirror.worker`, then set
  `MIRROR_WORKER=0` on the web processes) if extraction load starts to slow page requests.
- The hybrid search index is off in hosted mode, so retrieval uses plain matching only.
- Personal API tokens work (Connect tab; hooks and direct API calls). The MCP/gateway edge
  server has a multi-user mode (`MIRROR_EDGE_MULTIUSER=1`, tokens passed through to the
  app, callers bring their own upstream key) but is not part of the Fly deployment yet:
  it needs a second process and its own hostname.
- Rate limits exist (per user, per worker: `MIRROR_RATE_ALL` 120/min, `MIRROR_RATE_COSTLY`
  10/min) but there is no spend budget per user. With 4 workers the real ceiling is 4x.
- `/privacy` is a plain-language notice generated from what the code does, and "Download my
  data" exports everything as JSON. Set `MIRROR_OPERATOR_NAME` and `MIRROR_CONTACT_EMAIL`
  so the notice names who runs the service. It is not legal advice: have it reviewed, and
  provide a Korean version for Korean users (PIPA). Update `mirror/privacy.py` whenever the
  app starts sending data somewhere new (a test fails if the page loads from a new host).
