-- Server-owned starting-view onboarding marker.
--
-- Null means the account has not completed onboarding, including every row
-- that already exists. This migration does not backfill. 1 means onboarding
-- was completed with the stored state_code (null state_code is United States).
-- The marker is not a row revision or an idempotency key.

begin;

alter table atlas_accounts.user_profiles
    add column starting_view_version integer;

alter table atlas_accounts.user_profiles
    add constraint user_profiles_starting_view_version_is_supported check (
        starting_view_version is null
        or starting_view_version = 1
    );

comment on column atlas_accounts.user_profiles.starting_view_version is
    'Server-owned starting-view onboarding marker. Null is incomplete, including legacy rows. 1 means onboarding completed with the stored state_code. Not a write revision or idempotency key.';

commit;
