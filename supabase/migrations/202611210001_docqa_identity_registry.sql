create table if not exists public.docqa_identity_users (
    user_id uuid primary key,
    auth_provider text not null,
    issuer text not null,
    subject text not null,
    email text,
    email_verified boolean not null default false,
    app_metadata jsonb not null default '{}'::jsonb,
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now(),
    constraint docqa_identity_users_provider_subject_key
        unique (auth_provider, issuer, subject)
);

alter table public.docqa_identity_users enable row level security;
revoke all on public.docqa_identity_users from public, anon, authenticated;
grant select, insert, update, delete on public.docqa_identity_users to service_role;

insert into public.docqa_identity_users (
    user_id, auth_provider, issuer, subject, email, email_verified, app_metadata
)
select u.id, 'supabase', 'supabase', u.id::text, u.email,
       u.email_confirmed_at is not null,
       coalesce(u.raw_app_meta_data, '{}'::jsonb)
from auth.users u
on conflict (user_id) do update
set auth_provider = excluded.auth_provider,
    issuer = excluded.issuer,
    subject = excluded.subject,
    email = excluded.email,
    email_verified = excluded.email_verified,
    app_metadata = excluded.app_metadata,
    updated_at = now();

create or replace function public.docqa_sync_supabase_identity_user()
returns trigger
language plpgsql
security definer
set search_path = pg_catalog, public, pg_temp
as $$
begin
    if tg_op = 'DELETE' then
        delete from public.docqa_identity_users
        where auth_provider = 'supabase'
          and issuer = 'supabase'
          and subject = old.id::text;
        return old;
    end if;

    insert into public.docqa_identity_users (
        user_id, auth_provider, issuer, subject, email, email_verified, app_metadata
    )
    values (
        new.id, 'supabase', 'supabase', new.id::text, new.email,
        new.email_confirmed_at is not null,
        coalesce(new.raw_app_meta_data, '{}'::jsonb)
    )
    on conflict (user_id) do update
    set auth_provider = excluded.auth_provider,
        issuer = excluded.issuer,
        subject = excluded.subject,
        email = excluded.email,
        email_verified = excluded.email_verified,
        app_metadata = excluded.app_metadata,
        updated_at = now();
    return new;
end;
$$;

revoke all on function public.docqa_sync_supabase_identity_user()
    from public, anon, authenticated, service_role;

drop trigger if exists docqa_sync_identity_user on auth.users;
create trigger docqa_sync_identity_user
after insert or update or delete on auth.users
for each row execute function public.docqa_sync_supabase_identity_user();
