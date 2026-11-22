create table if not exists public.docqa_workspace_offboarding_requests (
    request_id uuid primary key default gen_random_uuid(),
    tenant_id text not null,
    requester_user_id uuid not null references auth.users(id) on delete restrict,
    status text not null default 'pending'
        check (status in ('pending', 'approved', 'rejected')),
    requested_at timestamptz not null default now(),
    reviewed_by uuid references auth.users(id) on delete set null,
    reviewed_at timestamptz,
    review_note text not null default ''
        check (length(review_note) <= 500)
);

create unique index if not exists docqa_one_pending_offboarding_request_idx
    on public.docqa_workspace_offboarding_requests (tenant_id)
    where status = 'pending';

alter table public.docqa_workspace_offboarding_requests enable row level security;
revoke all on public.docqa_workspace_offboarding_requests
    from public, anon, authenticated, service_role;

create or replace function public.docqa_export_workspace(p_tenant_id text)
returns jsonb
language sql
stable
security definer
set search_path = pg_catalog, public, auth, pg_temp
as $$
    select jsonb_build_object(
        'exported_at', now(),
        'workspace',
            (select jsonb_build_object(
                'tenant_id', w.tenant_id,
                'workspace_type', w.workspace_type,
                'provider', w.provider,
                'external_account_id', w.external_account_id,
                'name', w.name,
                'owner_user_id', w.owner_user_id,
                'created_at', w.created_at
            )
             from public.docqa_workspaces w
             where w.tenant_id = p_tenant_id),
        'members',
            coalesce(
                (select jsonb_agg(jsonb_build_object(
                    'user_id', m.user_id,
                    'email', u.email,
                    'groups', m.groups,
                    'roles', m.roles,
                    'joined_at', m.joined_at
                ) order by m.user_id)
                 from public.docqa_workspace_members m
                 left join auth.users u on u.id = m.user_id
                 where m.tenant_id = p_tenant_id),
                '[]'::jsonb
            ),
        'groups',
            coalesce(
                (select jsonb_agg(jsonb_build_object(
                    'name', g.name,
                    'created_by', g.created_by,
                    'created_at', g.created_at
                ) order by g.name)
                 from public.docqa_workspace_groups g
                 where g.tenant_id = p_tenant_id),
                '[]'::jsonb
            ),
        'documents',
            coalesce(
                (select jsonb_agg(jsonb_build_object(
                    'id', d.id,
                    'name', d.name,
                    'acl_groups', d.acl_groups,
                    'uploaded_by', d.uploaded_by,
                    'created_at', d.created_at,
                    'chunks',
                        coalesce(
                            (select jsonb_agg(jsonb_build_object(
                                'location', c.location,
                                'content', c.content
                            ) order by c.location, c.id)
                             from public.docqa_chunks c
                             where c.document_id = d.id
                               and c.tenant_id = d.tenant_id),
                            '[]'::jsonb
                        )
                ) order by d.name, d.id)
                 from public.docqa_documents d
                 where d.tenant_id = p_tenant_id),
                '[]'::jsonb
            ),
        'audit_events',
            coalesce(
                (select jsonb_agg(jsonb_build_object(
                    'user_id', e.user_id,
                    'action', e.action,
                    'question', e.question,
                    'document_name', e.document_name,
                    'documents_retrieved', e.documents_retrieved,
                    'documents_cited', e.documents_cited,
                    'outcome', e.outcome,
                    'created_at', e.created_at
                ) order by e.created_at, e.id)
                 from public.docqa_audit_events e
                 where e.tenant_id = p_tenant_id),
                '[]'::jsonb
            ),
        'registered_organization',
            (select jsonb_build_object(
                 'microsoft_tenant_id', o.microsoft_tenant_id,
                 'name', o.name,
                 'initial_admin_emails', o.initial_admin_emails,
                 'created_at', o.created_at
            )
             from public.docqa_registered_organizations o
             where o.microsoft_tenant_id = (
                  select w.external_account_id::uuid
                  from public.docqa_workspaces w
                  where w.tenant_id = p_tenant_id
                    and w.provider = 'azure'
                    and w.workspace_type = 'organization'
             )),
        'organization_groups',
            coalesce(
                 (select jsonb_agg(jsonb_build_object(
                     'graph_group_id', g.graph_group_id,
                     'graph_group_name', g.graph_group_name,
                     'workspace_group_name', g.workspace_group_name,
                     'created_at', g.created_at
                 ) order by g.graph_group_name)
                  from public.docqa_registered_organization_groups g
                  where g.microsoft_tenant_id = (
                      select w.external_account_id::uuid
                      from public.docqa_workspaces w
                      where w.tenant_id = p_tenant_id
                        and w.provider = 'azure'
                        and w.workspace_type = 'organization'
                  )),
                 '[]'::jsonb
            ),
        'organization_requests',
            coalesce(
                 (select jsonb_agg(jsonb_build_object(
                     'request_id', r.request_id,
                     'name', r.name,
                     'verified_domains', r.verified_domains,
                     'requested_by', r.requested_by,
                     'requester_email', r.requester_email,
                     'selected_groups', r.selected_groups,
                     'status', r.status,
                     'requested_at', r.requested_at,
                     'reviewed_by', r.reviewed_by,
                     'reviewed_at', r.reviewed_at,
                     'review_note', r.review_note
                 ) order by r.requested_at, r.request_id)
                  from public.docqa_organization_requests r
                  where r.microsoft_tenant_id = (
                      select w.external_account_id::uuid
                      from public.docqa_workspaces w
                      where w.tenant_id = p_tenant_id
                        and w.provider = 'azure'
                        and w.workspace_type = 'organization'
                  )),
                 '[]'::jsonb
            ),
        'organization_request_audit',
            coalesce(
                 (select jsonb_agg(jsonb_build_object(
                     'request_id', a.request_id,
                     'actor_user_id', a.actor_user_id,
                     'action', a.action,
                     'created_at', a.created_at
                 ) order by a.created_at, a.id)
                  from public.docqa_organization_request_audit a
                  join public.docqa_organization_requests r
                    on r.request_id = a.request_id
                  where r.microsoft_tenant_id = (
                      select w.external_account_id::uuid
                      from public.docqa_workspaces w
                      where w.tenant_id = p_tenant_id
                        and w.provider = 'azure'
                        and w.workspace_type = 'organization'
                  )),
                 '[]'::jsonb
            ),
        'offboarding_requests',
            coalesce(
                 (select jsonb_agg(jsonb_build_object(
                     'request_id', r.request_id,
                     'requester_user_id', r.requester_user_id,
                     'status', r.status,
                     'requested_at', r.requested_at,
                     'reviewed_by', r.reviewed_by,
                     'reviewed_at', r.reviewed_at,
                     'review_note', r.review_note
                 ) order by r.requested_at, r.request_id)
                  from public.docqa_workspace_offboarding_requests r
                  where r.tenant_id = p_tenant_id),
                 '[]'::jsonb
            )
    );
$$;

create or replace function public.docqa_create_workspace_offboarding_request(
    p_tenant_id text,
    p_requester_user_id uuid
)
returns jsonb
language plpgsql
security definer
set search_path = pg_catalog, public, auth, pg_temp
as $$
declare
    v_request public.docqa_workspace_offboarding_requests%rowtype;
begin
    if p_tenant_id is null or length(trim(p_tenant_id)) = 0
       or p_requester_user_id is null then
        raise exception 'invalid offboarding request';
    end if;

    perform pg_advisory_xact_lock(hashtextextended(p_tenant_id, 0));
    if not exists (
        select 1 from public.docqa_workspaces w
        where w.tenant_id = p_tenant_id
    ) then
        raise exception 'workspace to offboard was not found';
    end if;
    if not exists (
        select 1 from public.docqa_workspace_members m
        where m.tenant_id = p_tenant_id
          and m.user_id = p_requester_user_id
          and ('admin' = any(m.roles) or 'owner' = any(m.groups))
    ) then
        raise exception 'workspace administrator access is required';
    end if;
    if exists (
        select 1 from public.docqa_workspace_offboarding_requests r
        where r.tenant_id = p_tenant_id and r.status = 'approved'
    ) then
        raise exception 'workspace has already been closed';
    end if;
    if exists (
        select 1 from public.docqa_workspace_offboarding_requests r
        where r.tenant_id = p_tenant_id and r.status = 'pending'
    ) then
        raise exception 'offboarding request is already pending';
    end if;

    insert into public.docqa_workspace_offboarding_requests (
        tenant_id, requester_user_id
    )
    values (p_tenant_id, p_requester_user_id)
    returning * into v_request;

    return jsonb_build_object(
        'request_id', v_request.request_id,
        'tenant_id', v_request.tenant_id,
        'requester_user_id', v_request.requester_user_id,
        'status', v_request.status,
        'requested_at', v_request.requested_at
    );
end;
$$;

create or replace function public.docqa_list_workspace_offboarding_requests(
    p_actor_user_id uuid
)
returns table (
    request_id uuid,
    tenant_id text,
    workspace_name text,
    workspace_type text,
    requester_user_id uuid,
    requester_email text,
    requested_at timestamptz
)
language sql
stable
security definer
set search_path = pg_catalog, public, auth, pg_temp
as $$
    select r.request_id,
           r.tenant_id,
           w.name,
           w.workspace_type,
           r.requester_user_id,
           u.email,
           r.requested_at
    from public.docqa_workspace_offboarding_requests r
    join public.docqa_workspaces w on w.tenant_id = r.tenant_id
    left join auth.users u on u.id = r.requester_user_id
    where r.status = 'pending'
      and exists (
          select 1 from public.docqa_platform_admins a
          where a.user_id = p_actor_user_id
      )
    order by r.requested_at, r.request_id;
$$;

create or replace function public.docqa_review_workspace_offboarding_request(
    p_request_id uuid,
    p_actor_user_id uuid,
    p_approve boolean,
    p_review_note text default ''
)
returns jsonb
language plpgsql
security definer
set search_path = pg_catalog, public, auth, pg_temp
as $$
declare
    v_request public.docqa_workspace_offboarding_requests%rowtype;
    v_workspace public.docqa_workspaces%rowtype;
    v_note text := btrim(coalesce(p_review_note, ''));
begin
    if p_request_id is null or p_actor_user_id is null
       or p_approve is null or length(v_note) > 500 then
        raise exception 'invalid offboarding review';
    end if;
    if not exists (
        select 1 from public.docqa_platform_admins a
        where a.user_id = p_actor_user_id
    ) then
        raise exception 'platform administrator access is required';
    end if;

    select * into v_request
    from public.docqa_workspace_offboarding_requests r
    where r.request_id = p_request_id
    for update;
    if not found or v_request.status <> 'pending' then
        raise exception 'offboarding request is no longer pending';
    end if;

    perform pg_advisory_xact_lock(hashtextextended(v_request.tenant_id, 0));
    select * into v_workspace
    from public.docqa_workspaces w
    where w.tenant_id = v_request.tenant_id
    for update;
    if not found then
        raise exception 'workspace to offboard was not found';
    end if;

    update public.docqa_workspace_offboarding_requests
       set status = case when p_approve then 'approved' else 'rejected' end,
           reviewed_by = p_actor_user_id,
           reviewed_at = now(),
           review_note = v_note
     where request_id = p_request_id;

    if p_approve then
        update auth.users u
           set raw_app_meta_data =
               coalesce(u.raw_app_meta_data, '{}'::jsonb)
               - array['tenant_id', 'groups', 'roles', 'workspace_type',
                       'workspace_name', 'graph_groups']
         where u.raw_app_meta_data ->> 'tenant_id' = v_request.tenant_id;

        delete from public.docqa_documents d
        where d.tenant_id = v_request.tenant_id;
        delete from public.docqa_audit_events e
        where e.tenant_id = v_request.tenant_id;
        delete from public.docqa_workspaces w
        where w.tenant_id = v_request.tenant_id;

        if v_workspace.provider = 'azure'
           and v_workspace.workspace_type = 'organization' then
            delete from public.docqa_organization_request_audit a
            where a.request_id in (
                select r.request_id
                from public.docqa_organization_requests r
                where r.microsoft_tenant_id = v_workspace.external_account_id::uuid
            );
            delete from public.docqa_organization_requests r
            where r.microsoft_tenant_id = v_workspace.external_account_id::uuid;
            delete from public.docqa_registered_organizations o
            where o.microsoft_tenant_id = v_workspace.external_account_id::uuid;
        end if;
    end if;

    return jsonb_build_object(
        'request_id', p_request_id,
        'tenant_id', v_request.tenant_id,
        'status', case when p_approve then 'approved' else 'rejected' end,
        'reviewed_by', p_actor_user_id,
        'reviewed_at', now(),
        'review_note', v_note
    );
end;
$$;

create or replace function public.docqa_is_workspace_closed(p_tenant_id text)
returns boolean
language sql
stable
security definer
set search_path = pg_catalog, public, pg_temp
as $$
    select exists (
        select 1
        from public.docqa_workspace_offboarding_requests r
        where r.tenant_id = p_tenant_id
          and r.status = 'approved'
    );
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

    perform pg_advisory_xact_lock(hashtextextended(p_tenant_id, 0));
    if exists (
        select 1 from public.docqa_workspace_offboarding_requests r
        where r.tenant_id = p_tenant_id and r.status = 'approved'
    ) then
        raise exception 'workspace has been closed';
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

revoke all on function public.docqa_export_workspace(text)
    from public, anon, authenticated;
revoke all on function public.docqa_create_workspace_offboarding_request(text, uuid)
    from public, anon, authenticated;
revoke all on function public.docqa_list_workspace_offboarding_requests(uuid)
    from public, anon, authenticated;
revoke all on function public.docqa_review_workspace_offboarding_request(uuid, uuid, boolean, text)
    from public, anon, authenticated;
revoke all on function public.docqa_is_workspace_closed(text)
    from public, anon, authenticated;
revoke all on function public.docqa_register_current_member(
    text, uuid, text, text, text, text, text[], text[], integer
) from public, anon, authenticated;

grant execute on function public.docqa_export_workspace(text) to service_role;
grant execute on function public.docqa_create_workspace_offboarding_request(text, uuid)
    to service_role;
grant execute on function public.docqa_list_workspace_offboarding_requests(uuid)
    to service_role;
grant execute on function public.docqa_review_workspace_offboarding_request(uuid, uuid, boolean, text)
    to service_role;
grant execute on function public.docqa_is_workspace_closed(text) to service_role;
grant execute on function public.docqa_register_current_member(
    text, uuid, text, text, text, text, text[], text[], integer
) to service_role;
