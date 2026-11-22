create table if not exists public.docqa_organization_requests (
    request_id uuid primary key,
    microsoft_tenant_id uuid not null,
    name text not null check (length(btrim(name)) between 1 and 80),
    verified_domains text[] not null default array[]::text[],
    requested_by uuid not null references auth.users(id) on delete restrict,
    requester_email text not null,
    selected_groups jsonb not null,
    status text not null default 'pending'
        check (status in ('pending', 'approved', 'rejected')),
    requested_at timestamptz not null default now(),
    reviewed_by uuid references auth.users(id) on delete set null,
    reviewed_at timestamptz,
    review_note text not null default ''
);

create unique index if not exists docqa_one_pending_organization_request_idx
    on public.docqa_organization_requests (microsoft_tenant_id)
    where status = 'pending';

create table if not exists public.docqa_registered_organization_groups (
    microsoft_tenant_id uuid not null
        references public.docqa_registered_organizations(microsoft_tenant_id) on delete cascade,
    graph_group_id uuid not null,
    graph_group_name text not null,
    workspace_group_name text not null check (
        length(btrim(workspace_group_name)) between 1 and 100
        and position(',' in workspace_group_name) = 0
        and lower(btrim(workspace_group_name)) not in ('all', 'admin', 'owner', 'staff')
    ),
    created_at timestamptz not null default now(),
    primary key (microsoft_tenant_id, graph_group_id),
    unique (microsoft_tenant_id, workspace_group_name)
);

create table if not exists public.docqa_organization_request_audit (
    id bigint generated always as identity primary key,
    request_id uuid not null references public.docqa_organization_requests(request_id)
        on delete restrict,
    actor_user_id uuid not null references auth.users(id) on delete restrict,
    action text not null check (action in ('submitted', 'approved', 'rejected')),
    created_at timestamptz not null default now()
);

create index if not exists docqa_organization_request_audit_request_idx
    on public.docqa_organization_request_audit (request_id, created_at desc, id desc);

alter table public.docqa_organization_requests enable row level security;
alter table public.docqa_registered_organization_groups enable row level security;
alter table public.docqa_organization_request_audit enable row level security;
revoke all on public.docqa_organization_requests,
              public.docqa_registered_organization_groups,
              public.docqa_organization_request_audit
    from public, anon, authenticated, service_role;

create or replace function public.docqa_create_organization_request(
    p_request_id uuid,
    p_microsoft_tenant_id uuid,
    p_name text,
    p_verified_domains text[],
    p_requested_by uuid,
    p_selected_groups jsonb
)
returns table (
    request_id uuid,
    microsoft_tenant_id uuid,
    name text,
    requester_email text,
    status text,
    requested_at timestamptz
)
language plpgsql
security definer
set search_path = pg_catalog, public, auth, pg_temp
as $$
#variable_conflict use_column
declare
    v_name text := btrim(coalesce(p_name, ''));
    v_email text;
    v_request public.docqa_organization_requests%rowtype;
begin
    if p_request_id is null or p_microsoft_tenant_id is null or p_requested_by is null
       or length(v_name) < 1 or length(v_name) > 80
       or p_selected_groups is null
       or jsonb_typeof(p_selected_groups) <> 'array'
       or jsonb_array_length(p_selected_groups) < 1
       or jsonb_array_length(p_selected_groups) > 100 then
        raise exception 'invalid organization request';
    end if;
    perform pg_advisory_xact_lock(
        hashtextextended(p_microsoft_tenant_id::text, 0)
    );

    select lower(u.email) into v_email
    from auth.users u
    where u.id = p_requested_by
      and u.email_confirmed_at is not null;
    if v_email is null then
        raise exception 'confirmed organization requester not found';
    end if;

    if exists (
        select 1
        from jsonb_array_elements(p_selected_groups) as item(group_data)
        where jsonb_typeof(group_data) <> 'object'
           or coalesce(group_data->>'group_id', '') !~*
                '^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$'
           or length(btrim(coalesce(group_data->>'display_name', ''))) = 0
           or length(btrim(coalesce(group_data->>'workspace_group_name', ''))) not between 1 and 100
           or position(',' in coalesce(group_data->>'workspace_group_name', '')) > 0
           or lower(btrim(coalesce(group_data->>'workspace_group_name', '')))
                in ('all', 'admin', 'owner', 'staff')
    ) then
        raise exception 'invalid organization group mapping';
    end if;

    if (
        select count(distinct lower(btrim(group_data->>'workspace_group_name')))
        from jsonb_array_elements(p_selected_groups) as item(group_data)
    ) <> jsonb_array_length(p_selected_groups)
       or (
        select count(distinct lower(group_data->>'group_id'))
        from jsonb_array_elements(p_selected_groups) as item(group_data)
    ) <> jsonb_array_length(p_selected_groups) then
        raise exception 'duplicate organization group mapping';
    end if;

    if exists (
        select 1 from public.docqa_registered_organizations o
        where o.microsoft_tenant_id = p_microsoft_tenant_id
    ) then
        raise exception 'organization already registered';
    end if;

    select * into v_request
    from public.docqa_organization_requests r
    where r.microsoft_tenant_id = p_microsoft_tenant_id
      and r.status = 'pending'
    for update;

    if found then
        update public.docqa_organization_requests r
           set name = v_name,
               verified_domains = coalesce(p_verified_domains, array[]::text[]),
               requested_by = p_requested_by,
               requester_email = v_email,
               selected_groups = p_selected_groups,
               requested_at = now(),
               reviewed_by = null,
               reviewed_at = null,
               review_note = ''
         where r.request_id = v_request.request_id
        returning r.* into v_request;
    else
        insert into public.docqa_organization_requests (
            request_id, microsoft_tenant_id, name, verified_domains,
            requested_by, requester_email, selected_groups
        )
        values (
            p_request_id, p_microsoft_tenant_id, v_name,
            coalesce(p_verified_domains, array[]::text[]),
            p_requested_by, v_email, p_selected_groups
        )
        returning * into v_request;
    end if;

    insert into public.docqa_organization_request_audit (
        request_id, actor_user_id, action
    ) values (v_request.request_id, p_requested_by, 'submitted');

    return query select v_request.request_id, v_request.microsoft_tenant_id,
        v_request.name, v_request.requester_email, v_request.status, v_request.requested_at;
end;
$$;

create or replace function public.docqa_list_organization_requests(
    p_actor_user_id uuid
)
returns table (
    request_id uuid,
    microsoft_tenant_id uuid,
    name text,
    verified_domains text[],
    requester_email text,
    selected_groups jsonb,
    status text,
    requested_at timestamptz
)
language plpgsql
stable
security definer
set search_path = pg_catalog, public, pg_temp
as $$
#variable_conflict use_column
begin
    if p_actor_user_id is null or not exists (
        select 1 from public.docqa_platform_admins a
        where a.user_id = p_actor_user_id
    ) then
        raise exception 'platform administrator role is required';
    end if;
    return query
    select r.request_id, r.microsoft_tenant_id, r.name, r.verified_domains,
           r.requester_email, r.selected_groups, r.status, r.requested_at
    from public.docqa_organization_requests r
    where r.status = 'pending'
    order by r.requested_at, r.request_id;
end;
$$;

create or replace function public.docqa_review_organization_request(
    p_request_id uuid,
    p_actor_user_id uuid,
    p_approve boolean,
    p_review_note text default ''
)
returns table (
    request_id uuid,
    status text,
    microsoft_tenant_id uuid,
    name text,
    requester_email text,
    requested_at timestamptz,
    verified_domains text[],
    selected_groups jsonb
)
language plpgsql
security definer
set search_path = pg_catalog, public, auth, pg_temp
as $$
#variable_conflict use_column
declare
    v_request public.docqa_organization_requests%rowtype;
    v_status text := case when p_approve then 'approved' else 'rejected' end;
begin
    if p_request_id is null or p_actor_user_id is null or p_approve is null
       or length(coalesce(p_review_note, '')) > 500 then
        raise exception 'invalid organization review';
    end if;
    if not exists (
        select 1 from public.docqa_platform_admins a
        where a.user_id = p_actor_user_id
    ) then
        raise exception 'platform administrator role is required';
    end if;

    select * into v_request
    from public.docqa_organization_requests r
    where r.request_id = p_request_id;
    if not found then
        raise exception 'organization request is not pending';
    end if;
    perform pg_advisory_xact_lock(
        hashtextextended(v_request.microsoft_tenant_id::text, 0)
    );
    select * into v_request
    from public.docqa_organization_requests r
    where r.request_id = p_request_id
    for update;
    if not found or v_request.status <> 'pending' then
        raise exception 'organization request is not pending';
    end if;

    if p_approve then
        insert into public.docqa_registered_organizations (
            microsoft_tenant_id, name, initial_admin_emails
        ) values (
            v_request.microsoft_tenant_id, v_request.name,
            array[v_request.requester_email]::text[]
        )
        on conflict on constraint docqa_registered_organizations_pkey do nothing;
        if not found then
            raise exception 'organization already registered';
        end if;

        insert into public.docqa_registered_organization_groups (
            microsoft_tenant_id, graph_group_id, graph_group_name, workspace_group_name
        )
        select v_request.microsoft_tenant_id,
               (group_data->>'group_id')::uuid,
               btrim(group_data->>'display_name'),
               btrim(group_data->>'workspace_group_name')
        from jsonb_array_elements(v_request.selected_groups) as item(group_data);
    end if;

    update public.docqa_organization_requests
       set status = v_status,
           reviewed_by = p_actor_user_id,
           reviewed_at = now(),
           review_note = btrim(coalesce(p_review_note, ''))
     where request_id = p_request_id
     returning * into v_request;

    insert into public.docqa_organization_request_audit (
        request_id, actor_user_id, action
    ) values (p_request_id, p_actor_user_id, v_status);

    return query select v_request.request_id, v_request.status,
        v_request.microsoft_tenant_id, v_request.name, v_request.requester_email,
        v_request.requested_at, v_request.verified_domains, v_request.selected_groups;
end;
$$;

create or replace function public.docqa_get_organization_request_status(
    p_microsoft_tenant_id uuid
)
returns table (
    status text,
    name text
)
language sql
stable
security definer
set search_path = pg_catalog, public, pg_temp
as $$
    select r.status, r.name
    from public.docqa_organization_requests r
    where r.microsoft_tenant_id = p_microsoft_tenant_id
    order by r.requested_at desc
    limit 1;
$$;

create or replace function public.docqa_get_registered_organization_groups(
    p_microsoft_tenant_id uuid
)
returns table (
    graph_group_id uuid,
    graph_group_name text,
    workspace_group_name text
)
language sql
stable
security definer
set search_path = pg_catalog, public, pg_temp
as $$
    select g.graph_group_id, g.graph_group_name, g.workspace_group_name
    from public.docqa_registered_organization_groups g
    where g.microsoft_tenant_id = p_microsoft_tenant_id
    order by lower(g.workspace_group_name), g.graph_group_id;
$$;

revoke all on function public.docqa_create_organization_request(
    uuid, uuid, text, text[], uuid, jsonb
) from public, anon, authenticated;
revoke all on function public.docqa_list_organization_requests(uuid)
    from public, anon, authenticated;
revoke all on function public.docqa_review_organization_request(uuid, uuid, boolean, text)
    from public, anon, authenticated;
revoke all on function public.docqa_get_organization_request_status(uuid)
    from public, anon, authenticated;
revoke all on function public.docqa_get_registered_organization_groups(uuid)
    from public, anon, authenticated;

grant execute on function public.docqa_create_organization_request(
    uuid, uuid, text, text[], uuid, jsonb
) to service_role;
grant execute on function public.docqa_list_organization_requests(uuid) to service_role;
grant execute on function public.docqa_review_organization_request(uuid, uuid, boolean, text)
    to service_role;
grant execute on function public.docqa_get_organization_request_status(uuid) to service_role;
grant execute on function public.docqa_get_registered_organization_groups(uuid) to service_role;
