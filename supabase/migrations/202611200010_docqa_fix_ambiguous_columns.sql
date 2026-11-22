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

    insert into public.docqa_organization_requests (
        request_id, microsoft_tenant_id, name, verified_domains,
        requested_by, requester_email, selected_groups
    )
    values (
        p_request_id, p_microsoft_tenant_id, v_name,
        coalesce(p_verified_domains, array[]::text[]),
        p_requested_by, v_email, p_selected_groups
    )
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
