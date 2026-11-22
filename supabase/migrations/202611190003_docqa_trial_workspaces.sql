create table if not exists public.docqa_workspaces (
    tenant_id text primary key,
    workspace_type text not null check (workspace_type in ('personal', 'organization')),
    provider text not null check (provider in ('google', 'azure', 'legacy')),
    external_account_id text not null,
    name text not null,
    owner_user_id uuid not null,
    member_limit integer not null check (member_limit > 0),
    created_at timestamptz not null default now(),
    unique (provider, external_account_id)
);

create table if not exists public.docqa_workspace_members (
    tenant_id text not null references public.docqa_workspaces(tenant_id) on delete cascade,
    user_id uuid not null,
    groups text[] not null default array[]::text[],
    roles text[] not null default array[]::text[],
    joined_at timestamptz not null default now(),
    primary key (tenant_id, user_id)
);

alter table public.docqa_workspaces enable row level security;
alter table public.docqa_workspace_members enable row level security;
revoke all on public.docqa_workspaces, public.docqa_workspace_members
    from public, anon, authenticated;
grant all on public.docqa_workspaces, public.docqa_workspace_members to service_role;

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
set search_path = pg_catalog, public, extensions, pg_temp
as $$
declare
    v_workspace public.docqa_workspaces%rowtype;
    v_member public.docqa_workspace_members%rowtype;
    v_count bigint;
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
    if p_name is null or length(trim(p_name)) = 0 or length(p_name) > 80 then
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
        trim(p_name),
        p_user_id,
        p_member_limit
    )
    on conflict (provider, external_account_id) do nothing;

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

        if v_workspace.owner_user_id = p_user_id then
            insert into public.docqa_workspace_members (tenant_id, user_id, groups, roles)
            values (v_workspace.tenant_id, p_user_id, array['owner']::text[], array['admin']::text[])
            returning * into v_member;
        else
            insert into public.docqa_workspace_members (tenant_id, user_id, groups, roles)
            values (v_workspace.tenant_id, p_user_id, array['staff']::text[], array[]::text[])
            returning * into v_member;
        end if;
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

create or replace function public.docqa_trial_usage(p_tenant_id text)
returns table (
    workspace_type text,
    workspace_name text,
    member_count bigint,
    member_limit integer,
    document_count bigint,
    document_limit integer
)
language sql
stable
security definer
set search_path = pg_catalog, public, extensions, pg_temp
as $$
    select w.workspace_type,
           w.name,
           (select count(*) from public.docqa_workspace_members m where m.tenant_id = w.tenant_id),
           w.member_limit,
           (select count(*) from public.docqa_documents d where d.tenant_id = w.tenant_id),
           5
    from public.docqa_workspaces w
    where w.tenant_id = p_tenant_id;
$$;

create or replace function public.docqa_register_current_member(
    p_tenant_id text,
    p_user_id uuid,
    p_provider text,
    p_external_account_id text,
    p_workspace_type text,
    p_workspace_name text,
    p_groups text[],
    p_roles text[],
    p_member_limit integer
)
returns bigint
language plpgsql
security definer
set search_path = pg_catalog, public, extensions, pg_temp
as $$
declare
    v_workspace public.docqa_workspaces%rowtype;
    v_count bigint;
begin
    if p_tenant_id is null or length(trim(p_tenant_id)) = 0
       or p_user_id is null
       or p_provider is null
       or p_provider not in ('google', 'azure', 'legacy')
       or p_external_account_id is null or length(trim(p_external_account_id)) = 0
       or p_workspace_type is null
       or p_workspace_type not in ('personal', 'organization')
       or p_member_limit is null
       or (p_workspace_type = 'personal' and p_member_limit <> 10)
       or (p_workspace_type = 'organization' and p_member_limit <> 15) then
        raise exception 'invalid workspace membership';
    end if;
    if p_provider = 'google' and p_workspace_type <> 'personal' then
        raise exception 'Google preview accounts must use a personal workspace';
    end if;

    insert into public.docqa_workspaces (
        tenant_id, workspace_type, provider, external_account_id, name, owner_user_id, member_limit
    )
    values (
        p_tenant_id,
        p_workspace_type,
        p_provider,
        p_external_account_id,
        coalesce(nullif(trim(p_workspace_name), ''), 'Workspace'),
        p_user_id,
        p_member_limit
    )
    on conflict do nothing;

    select * into v_workspace
    from public.docqa_workspaces w
    where w.tenant_id = p_tenant_id
    for update;
    if not found
       or v_workspace.workspace_type <> p_workspace_type
       or v_workspace.member_limit <> p_member_limit then
        raise exception 'workspace metadata does not match trusted user claims';
    end if;

    if not exists (
        select 1 from public.docqa_workspace_members m
        where m.tenant_id = p_tenant_id and m.user_id = p_user_id
    ) then
        select count(*) into v_count
        from public.docqa_workspace_members m
        where m.tenant_id = p_tenant_id;
        if v_count >= v_workspace.member_limit then
            raise exception 'trial member limit reached';
        end if;

        insert into public.docqa_workspace_members (tenant_id, user_id, groups, roles)
        values (
            p_tenant_id,
            p_user_id,
            coalesce(p_groups, array[]::text[]),
            coalesce(p_roles, array[]::text[])
        );
    else
        update public.docqa_workspace_members
           set groups = coalesce(p_groups, array[]::text[]),
               roles = coalesce(p_roles, array[]::text[])
         where tenant_id = p_tenant_id and user_id = p_user_id;
    end if;

    select count(*) into v_count
    from public.docqa_workspace_members m
    where m.tenant_id = p_tenant_id;
    if v_count > v_workspace.member_limit then
        raise exception 'trial member limit reached';
    end if;
    return v_count;
end;
$$;

revoke all on function public.docqa_provision_trial_workspace(
    uuid, text, text, text, text, integer
) from public, anon, authenticated;
revoke all on function public.docqa_trial_usage(text)
    from public, anon, authenticated;
revoke all on function public.docqa_register_current_member(
    text, uuid, text, text, text, text, text[], text[], integer
) from public, anon, authenticated;
grant execute on function public.docqa_provision_trial_workspace(
    uuid, text, text, text, text, integer
) to service_role;
grant execute on function public.docqa_trial_usage(text)
    to service_role;
grant execute on function public.docqa_register_current_member(
    text, uuid, text, text, text, text, text[], text[], integer
) to service_role;
