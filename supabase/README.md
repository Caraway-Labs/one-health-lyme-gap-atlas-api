# Supabase schema delivery

This directory owns version-controlled Supabase migrations for Atlas account
data. It belongs with the API because browser clients never read or write these
tables directly; FastAPI remains the authenticated application boundary.

## Environment separation

Use the same ordered migration files for the two hosted projects. Link and push
only to Development while validating a change. Production promotion must use the
same reviewed migration history and explicit owner approval; never connect a
dashboard or GitHub integration that automatically deploys `main` to
Production.

Do not commit a project access token, database password, service-role key,
Google credential, SMTP credential, generated `supabase/.temp/` data, or an
environment file. Keep all values in the approved secret manager.

## Required validation before a remote push

1. Install and authenticate the Supabase CLI using an approved local token.
2. Run the migrations against a local Supabase development stack and execute
   RLS allow/deny tests under `supabase/tests/`.
3. Link the Development project by its project reference; do not put that
   reference in source-controlled configuration.
4. Use `supabase db push --dry-run`, inspect the migration list, and only then
   apply to Development.
5. Verify the table, RLS status, grants, and negative cross-user behavior in
   Development before any API or web rollout.

The initial migration deliberately gives no `anon` or `authenticated` database
grants. The FastAPI service validates the caller's JWT and uses only its
server-side Supabase secret key for data access.

## Data API configuration

Before FastAPI can use the profile table, add `atlas_accounts` to the project's
Data API **Exposed schemas** list: **Project Settings → Data API → Exposed
schemas**. This setting makes the schema available to PostgREST; it does not
grant browser access. Retain the migration's least-privilege permissions: do
not grant `anon` or `authenticated` schema or table privileges. Validate this
setting in Development before applying it in Production.
