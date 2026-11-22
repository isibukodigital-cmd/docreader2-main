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
