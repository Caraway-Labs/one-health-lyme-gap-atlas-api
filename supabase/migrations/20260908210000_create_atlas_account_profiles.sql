-- Optional-account MVP: private, server-mediated profile persistence.
--
-- This migration is intentionally independent of a project reference. Apply it
-- through the Supabase CLI to Development first, verify it there, and promote
-- the identical migration history to Production only through the approved
-- release path. Do not apply ad-hoc schema changes in the dashboard.

begin;

create schema if not exists atlas_accounts;

revoke all on schema atlas_accounts from public;
revoke all on schema atlas_accounts from anon;
revoke all on schema atlas_accounts from authenticated;
grant usage on schema atlas_accounts to service_role;

create type atlas_accounts.profile_role as enum (
    'general_public_citizen',
    'district_level_epidemiologist',
    'state_level_epidemiologist',
    'state_director_level_epidemiologist',
    'national_level_epidemiologist'
);

create table atlas_accounts.user_profiles (
    user_id uuid primary key references auth.users (id) on delete cascade,
    role atlas_accounts.profile_role,
    state_code varchar(2),
    organization varchar(120),
    job_title varchar(120),
    created_at timestamptz not null default timezone('utc', now()),
    updated_at timestamptz not null default timezone('utc', now()),
    last_active_at timestamptz not null default timezone('utc', now()),
    constraint user_profiles_state_code_is_us_state check (
        state_code is null
        or state_code in (
            'AL', 'AK', 'AZ', 'AR', 'CA', 'CO', 'CT', 'DE', 'FL', 'GA',
            'HI', 'ID', 'IL', 'IN', 'IA', 'KS', 'KY', 'LA', 'ME', 'MD',
            'MA', 'MI', 'MN', 'MS', 'MO', 'MT', 'NE', 'NV', 'NH', 'NJ',
            'NM', 'NY', 'NC', 'ND', 'OH', 'OK', 'OR', 'PA', 'RI', 'SC',
            'SD', 'TN', 'TX', 'UT', 'VT', 'VA', 'WA', 'WV', 'WI', 'WY',
            'DC'
        )
    ),
    constraint user_profiles_organization_is_bounded check (
        organization is null
        or (char_length(organization) between 1 and 120 and organization = btrim(organization))
    ),
    constraint user_profiles_job_title_is_bounded check (
        job_title is null
        or (char_length(job_title) between 1 and 120 and job_title = btrim(job_title))
    )
);

create index user_profiles_dormancy_idx on atlas_accounts.user_profiles (last_active_at);

alter table atlas_accounts.user_profiles enable row level security;

-- The browser is deliberately not granted database access. These policies are
-- retained as defense in depth for a future constrained user-context path; the
-- API's server-side identity remains the only supported access path.
create policy "user_profiles_select_own"
    on atlas_accounts.user_profiles
    for select
    to authenticated
    using ((select auth.uid()) = user_id);

create policy "user_profiles_insert_own"
    on atlas_accounts.user_profiles
    for insert
    to authenticated
    with check ((select auth.uid()) = user_id);

create policy "user_profiles_update_own"
    on atlas_accounts.user_profiles
    for update
    to authenticated
    using ((select auth.uid()) = user_id)
    with check ((select auth.uid()) = user_id);

create policy "user_profiles_delete_own"
    on atlas_accounts.user_profiles
    for delete
    to authenticated
    using ((select auth.uid()) = user_id);

revoke all on table atlas_accounts.user_profiles from public;
revoke all on table atlas_accounts.user_profiles from anon;
revoke all on table atlas_accounts.user_profiles from authenticated;
grant select, insert, update, delete on table atlas_accounts.user_profiles to service_role;

create function atlas_accounts.set_user_profiles_timestamps()
returns trigger
language plpgsql
set search_path = ''
as $$
begin
    new.updated_at = timezone('utc', now());
    return new;
end;
$$;

create trigger user_profiles_set_timestamps
before update on atlas_accounts.user_profiles
for each row
execute function atlas_accounts.set_user_profiles_timestamps();

-- The API will invoke this function only after a validated authenticated
-- request. Scheduling the one-year automatic account deletion is a separate
-- operational release step because it must remove the corresponding auth user,
-- not merely the profile row.
create function atlas_accounts.touch_user_profile_activity(target_user_id uuid)
returns void
language sql
security definer
set search_path = atlas_accounts, public
as $$
    update atlas_accounts.user_profiles
       set last_active_at = timezone('utc', now())
     where user_id = target_user_id;
$$;

revoke all on function atlas_accounts.touch_user_profile_activity(uuid) from public;
revoke all on function atlas_accounts.touch_user_profile_activity(uuid) from anon;
revoke all on function atlas_accounts.touch_user_profile_activity(uuid) from authenticated;
grant execute on function atlas_accounts.touch_user_profile_activity(uuid) to service_role;

commit;
