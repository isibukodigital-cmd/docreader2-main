create table if not exists public.docqa_platform_admins (
    user_id uuid primary key references auth.users(id) on delete restrict,
    granted_by uuid references auth.users(id) on delete set null,
    created_at timestamptz not null default now()
);

create table if not exists public.docqa_platform_admin_audit (
    id bigint generated always as identity primary key,
    actor_user_id uuid not null,
    target_user_id uuid not null,
    action text not null check (action in ('grant', 'revoke')),
    created_at timestamptz not null default now()
);

create index if not exists docqa_platform_admin_audit_created_at_idx
    on public.docqa_platform_admin_audit (created_at desc, id desc);

alter table public.docqa_platform_admins enable row level security;
alter table public.docqa_platform_admin_audit enable row level security;
revoke all on public.docqa_platform_admins, public.docqa_platform_admin_audit
    from public, anon, authenticated, service_role;

create or replace function public.docqa_is_platform_admin(p_user_id uuid)
returns boolean
language sql
stable
security definer
set search_path = pg_catalog, public, pg_temp
as $$
    select exists (
        select 1
        from public.docqa_platform_admins a
        where a.user_id = p_user_id
    );
$$;

create or replace function public.docqa_list_platform_admins()
returns table (
    user_id uuid,
    email text,
    granted_by uuid,
    created_at timestamptz
)
language sql
stable
security definer
set search_path = pg_catalog, public, auth, pg_temp
as $$
    select a.user_id, u.email, a.granted_by, a.created_at
    from public.docqa_platform_admins a
    left join auth.users u on u.id = a.user_id
    order by a.created_at, a.user_id;
$$;

create or replace function public.docqa_list_platform_admin_audit(p_limit integer default 100)
returns table (
    id bigint,
    actor_user_id uuid,
    actor_email text,
    target_user_id uuid,
    target_email text,
    action text,
    created_at timestamptz
)
language sql
stable
security definer
set search_path = pg_catalog, public, auth, pg_temp
as $$
    select e.id,
           e.actor_user_id,
           actor.email,
           e.target_user_id,
           target.email,
           e.action,
           e.created_at
    from public.docqa_platform_admin_audit e
    left join auth.users actor on actor.id = e.actor_user_id
    left join auth.users target on target.id = e.target_user_id
    order by e.created_at desc, e.id desc
    limit greatest(1, least(coalesce(p_limit, 100), 200));
$$;

create or replace function public.docqa_grant_platform_admin(
    p_actor_user_id uuid,
    p_target_email text
)
returns table (
    user_id uuid,
    email text,
    granted_by uuid,
    created_at timestamptz
)
language plpgsql
security definer
set search_path = pg_catalog, public, auth, pg_temp
as $$
#variable_conflict use_column
declare
    v_email text := lower(btrim(coalesce(p_target_email, '')));
    v_target_user_id uuid;
    v_inserted_user_id uuid;
begin
    if p_actor_user_id is null
       or not exists (
           select 1 from public.docqa_platform_admins a
           where a.user_id = p_actor_user_id
       ) then
        raise exception 'platform administrator access is required';
    end if;
    if v_email !~ '^[^@[:space:]]+@[^@[:space:]]+\.[^@[:space:]]+$' then
        raise exception 'a valid target email is required';
    end if;

    select u.id into v_target_user_id
    from auth.users u
    where lower(u.email) = v_email
      and u.email_confirmed_at is not null;
    if not found then
        raise exception 'confirmed platform administrator target not found';
    end if;

    insert into public.docqa_platform_admins as platform_admin (user_id, granted_by)
    values (v_target_user_id, p_actor_user_id)
    on conflict on constraint docqa_platform_admins_pkey do nothing
    returning platform_admin.user_id into v_inserted_user_id;

    if v_inserted_user_id is not null then
        insert into public.docqa_platform_admin_audit (
            actor_user_id, target_user_id, action
        )
        values (p_actor_user_id, v_target_user_id, 'grant');
    end if;

    return query
    select a.user_id, u.email::text, a.granted_by, a.created_at
    from public.docqa_platform_admins a
    left join auth.users u on u.id = a.user_id
    where a.user_id = v_target_user_id;
end;
$$;

create or replace function public.docqa_revoke_platform_admin(
    p_actor_user_id uuid,
    p_target_user_id uuid
)
returns boolean
language plpgsql
security definer
set search_path = pg_catalog, public, pg_temp
as $$
declare
    v_admin_count bigint;
begin
    perform pg_advisory_xact_lock(76192864);

    if p_actor_user_id is null
       or not exists (
           select 1 from public.docqa_platform_admins a
           where a.user_id = p_actor_user_id
       ) then
        raise exception 'platform administrator access is required';
    end if;
    if not exists (
        select 1 from public.docqa_platform_admins a
        where a.user_id = p_target_user_id
    ) then
        return false;
    end if;

    select count(*) into v_admin_count
    from public.docqa_platform_admins;
    if v_admin_count <= 1 then
        raise exception 'cannot remove the last platform administrator';
    end if;

    delete from public.docqa_platform_admins
    where user_id = p_target_user_id;
    insert into public.docqa_platform_admin_audit (
        actor_user_id, target_user_id, action
    )
    values (p_actor_user_id, p_target_user_id, 'revoke');
    return true;
end;
$$;

revoke all on function public.docqa_is_platform_admin(uuid)
    from public, anon, authenticated;
revoke all on function public.docqa_list_platform_admins()
    from public, anon, authenticated;
revoke all on function public.docqa_list_platform_admin_audit(integer)
    from public, anon, authenticated;
revoke all on function public.docqa_grant_platform_admin(uuid, text)
    from public, anon, authenticated;
revoke all on function public.docqa_revoke_platform_admin(uuid, uuid)
    from public, anon, authenticated;
grant execute on function public.docqa_is_platform_admin(uuid) to service_role;
grant execute on function public.docqa_list_platform_admins() to service_role;
grant execute on function public.docqa_list_platform_admin_audit(integer) to service_role;
grant execute on function public.docqa_grant_platform_admin(uuid, text) to service_role;
grant execute on function public.docqa_revoke_platform_admin(uuid, uuid) to service_role;
