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
        returning * into v_request;
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
