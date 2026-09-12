-- Authenticated data-rights ledger (ADR 0016).
-- Apply to Development first, then promote the identical history to Production.

begin;

create type atlas_accounts.privacy_action as enum ('export', 'deletion');
create type atlas_accounts.privacy_request_state as enum (
    'requested',
    'verified',
    'confirmed',
    'in_progress',
    'completed',
    'needs_support'
);

create table atlas_accounts.privacy_requests (
    request_id uuid primary key default gen_random_uuid(),
    user_id uuid not null,
    action atlas_accounts.privacy_action not null,
    state atlas_accounts.privacy_request_state not null default 'requested',
    nonce_hash text,
    nonce_expires_at timestamptz,
    created_at timestamptz not null default timezone('utc', now()),
    confirmed_at timestamptz,
    completed_at timestamptz,
    export_payload jsonb,
    export_expires_at timestamptz,
    processor_outcomes jsonb not null default '[]'::jsonb,
    error_class text,
    constraint privacy_requests_error_class_is_redacted check (
        error_class is null
        or error_class in (
            'upstream_http',
            'auth_admin_unavailable',
            'invalid_state',
            'processor_failure',
            'stale_in_progress'
        )
    )
);

create index privacy_requests_subject_idx
    on atlas_accounts.privacy_requests (user_id, created_at desc);

alter table atlas_accounts.privacy_requests enable row level security;

create policy "privacy_requests_select_own"
    on atlas_accounts.privacy_requests
    for select
    to authenticated
    using ((select auth.uid()) = user_id);

revoke all on table atlas_accounts.privacy_requests from public;
revoke all on table atlas_accounts.privacy_requests from anon;
revoke all on table atlas_accounts.privacy_requests from authenticated;
grant select, insert, update, delete on table atlas_accounts.privacy_requests to service_role;

commit;
