create table if not exists public.docqa_registered_organizations (
    microsoft_tenant_id uuid primary key,
    name text not null check (length(btrim(name)) between 1 and 80),
    initial_admin_emails text[] not null check (
        cardinality(initial_admin_emails) between 1 and 20
    ),
    created_at timestamptz not null default now()
);

alter table public.docqa_registered_organizations enable row level security;
revoke all on public.docqa_registered_organizations from public, anon, authenticated;
grant all on public.docqa_registered_organizations to service_role;

create or replace function public.docqa_get_registered_organization(
    p_microsoft_tenant_id uuid
)
returns table (
    microsoft_tenant_id uuid,
    name text,
    initial_admin_emails text[],
    created_at timestamptz
)
language sql
stable
security definer
set search_path = pg_catalog, public, pg_temp
as $$
    select o.microsoft_tenant_id, o.name, o.initial_admin_emails, o.created_at
    from public.docqa_registered_organizations o
    where o.microsoft_tenant_id = p_microsoft_tenant_id;
$$;

create or replace function public.docqa_list_registered_organizations()
returns table (
    microsoft_tenant_id uuid,
    name text,
    initial_admin_emails text[],
    created_at timestamptz
)
language sql
stable
security definer
set search_path = pg_catalog, public, pg_temp
as $$
    select o.microsoft_tenant_id, o.name, o.initial_admin_emails, o.created_at
    from public.docqa_registered_organizations o
    order by lower(o.name), o.microsoft_tenant_id;
$$;

create or replace function public.docqa_set_registered_organization(
    p_microsoft_tenant_id uuid,
    p_name text,
    p_initial_admin_emails text[]
)
returns table (
    microsoft_tenant_id uuid,
    name text,
    initial_admin_emails text[],
    created_at timestamptz
)
language plpgsql
security definer
set search_path = pg_catalog, public, pg_temp
as $$
#variable_conflict use_column
declare
    v_name text := btrim(coalesce(p_name, ''));
    v_emails text[];
begin
    if p_microsoft_tenant_id is null
       or length(v_name) < 1 or length(v_name) > 80
       or p_initial_admin_emails is null
       or cardinality(p_initial_admin_emails) < 1
       or cardinality(p_initial_admin_emails) > 20 then
        raise exception 'invalid organization registration';
    end if;

    select array_agg(distinct lower(btrim(email)) order by lower(btrim(email)))
      into v_emails
      from unnest(p_initial_admin_emails) as e(email)
     where email is not null;

    if v_emails is null
       or cardinality(v_emails) <> cardinality(p_initial_admin_emails)
       or exists (
           select 1
           from unnest(v_emails) as e(email)
           where email !~ '^[^@[:space:]]+@[^@[:space:]]+\.[^@[:space:]]+$'
       ) then
        raise exception 'invalid organization administrator email';
    end if;

    insert into public.docqa_registered_organizations (
        microsoft_tenant_id, name, initial_admin_emails
    )
    values (p_microsoft_tenant_id, v_name, v_emails)
    on conflict on constraint docqa_registered_organizations_pkey do update
       set name = excluded.name,
           initial_admin_emails = excluded.initial_admin_emails;

    return query
    select o.microsoft_tenant_id, o.name, o.initial_admin_emails, o.created_at
    from public.docqa_registered_organizations o
    where o.microsoft_tenant_id = p_microsoft_tenant_id;
end;
$$;

create or replace function public.docqa_remove_registered_organization(
    p_microsoft_tenant_id uuid
)
returns boolean
language plpgsql
security definer
set search_path = pg_catalog, public, pg_temp
as $$
declare
    v_deleted_rows integer;
begin
    delete from public.docqa_registered_organizations
    where microsoft_tenant_id = p_microsoft_tenant_id;
    get diagnostics v_deleted_rows = row_count;
    return v_deleted_rows > 0;
end;
$$;

revoke all on function public.docqa_get_registered_organization(uuid)
    from public, anon, authenticated;
revoke all on function public.docqa_list_registered_organizations()
    from public, anon, authenticated;
revoke all on function public.docqa_set_registered_organization(uuid, text, text[])
    from public, anon, authenticated;
revoke all on function public.docqa_remove_registered_organization(uuid)
    from public, anon, authenticated;
grant execute on function public.docqa_get_registered_organization(uuid) to service_role;
grant execute on function public.docqa_list_registered_organizations() to service_role;
grant execute on function public.docqa_set_registered_organization(uuid, text, text[])
    to service_role;
grant execute on function public.docqa_remove_registered_organization(uuid) to service_role;

create or replace function public.docqa_provision_trial_workspace(
    p_user_id uuid,
    p_provider text,
    p_external_account_id text,
    p_workspace_type text,
    p_name text,
    p_member_limit integer
)
returns table (
    tenant_id text,
    workspace_type text,
    workspace_name text,
    member_groups text[],
    member_roles text[],
    member_count bigint,
    member_limit integer
)
language plpgsql
security definer
set search_path = pg_catalog, public, auth, extensions, pg_temp
as $$
#variable_conflict use_column
declare
    v_workspace public.docqa_workspaces%rowtype;
    v_member public.docqa_workspace_members%rowtype;
    v_organization public.docqa_registered_organizations%rowtype;
    v_count bigint;
    v_is_initial_admin boolean := false;
    v_name text := btrim(coalesce(p_name, ''));
begin
    if p_user_id is null
       or p_provider is null
       or p_provider not in ('google', 'azure')
       or p_external_account_id is null
       or length(trim(p_external_account_id)) = 0
       or p_workspace_type is null
       or p_workspace_type not in ('personal', 'organization')
       or p_member_limit is null
       or p_member_limit < 1 then
        raise exception 'invalid workspace provisioning request';
    end if;
    if p_provider = 'google' and p_workspace_type <> 'personal' then
        raise exception 'Google preview accounts must use a personal workspace';
    end if;
    if (p_workspace_type = 'personal' and p_member_limit <> 10)
       or (p_workspace_type = 'organization' and p_member_limit <> 15) then
        raise exception 'invalid trial member limit';
    end if;

    if p_workspace_type = 'organization' then
        if p_provider <> 'azure'
           or p_external_account_id !~* '^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$' then
            raise exception 'organization workspaces require a registered Microsoft tenant';
        end if;
        select * into v_organization
        from public.docqa_registered_organizations o
        where o.microsoft_tenant_id = p_external_account_id::uuid;
        if not found then
            raise exception 'Microsoft organization is not registered';
        end if;
        v_name := v_organization.name;
        select exists (
            select 1
            from auth.users u
            where u.id = p_user_id
              and u.email_confirmed_at is not null
              and lower(u.email) = any(v_organization.initial_admin_emails)
        ) into v_is_initial_admin;
    end if;

    if p_workspace_type = 'personal' then
        if length(v_name) = 0 then
            v_name := 'Personal workspace';
        elsif length(v_name) > 80 then
            raise exception 'workspace name must contain 1 to 80 characters';
        end if;
    elsif length(v_name) = 0 or length(v_name) > 80 then
        raise exception 'workspace name must contain 1 to 80 characters';
    end if;

    insert into public.docqa_workspaces (
        tenant_id, workspace_type, provider, external_account_id, name, owner_user_id, member_limit
    )
    values (
        gen_random_uuid()::text,
        p_workspace_type,
        p_provider,
        p_external_account_id,
        v_name,
        p_user_id,
        p_member_limit
    )
    on conflict (provider, external_account_id) do update
       set name = excluded.name;

    select * into v_workspace
    from public.docqa_workspaces w
    where w.provider = p_provider
      and w.external_account_id = p_external_account_id
    for update;

    if not found or v_workspace.workspace_type <> p_workspace_type then
        raise exception 'workspace identity does not match the authenticated account';
    end if;

    select * into v_member
    from public.docqa_workspace_members m
    where m.tenant_id = v_workspace.tenant_id and m.user_id = p_user_id;

    if not found then
        select count(*) into v_count
        from public.docqa_workspace_members m
        where m.tenant_id = v_workspace.tenant_id;
        if v_count >= v_workspace.member_limit then
            raise exception 'trial member limit reached';
        end if;

        if (p_workspace_type = 'personal' and v_workspace.owner_user_id = p_user_id)
           or v_is_initial_admin then
            insert into public.docqa_workspace_members (tenant_id, user_id, groups, roles)
            values (v_workspace.tenant_id, p_user_id, array['owner']::text[], array['admin']::text[])
            returning * into v_member;
        else
            insert into public.docqa_workspace_members (tenant_id, user_id, groups, roles)
            values (v_workspace.tenant_id, p_user_id, array['staff']::text[], array[]::text[])
            returning * into v_member;
        end if;
    elsif v_is_initial_admin and not ('admin' = any(v_member.roles)) then
        update public.docqa_workspace_members
           set groups = case
                   when 'owner' = any(groups) then groups
                   else array_append(groups, 'owner')
               end,
               roles = case
                   when 'admin' = any(roles) then roles
                   else array_append(roles, 'admin')
               end
         where tenant_id = v_workspace.tenant_id and user_id = p_user_id
        returning * into v_member;
    end if;

    select count(*) into v_count
    from public.docqa_workspace_members m
    where m.tenant_id = v_workspace.tenant_id;

    return query select v_workspace.tenant_id,
                        v_workspace.workspace_type,
                        v_workspace.name,
                        v_member.groups,
                        v_member.roles,
                        v_count,
                        v_workspace.member_limit;
end;
$$;

revoke all on function public.docqa_provision_trial_workspace(
    uuid, text, text, text, text, integer
) from public, anon, authenticated;
grant execute on function public.docqa_provision_trial_workspace(
    uuid, text, text, text, text, integer
) to service_role;
