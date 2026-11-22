-- Allow administrators to create workspace groups before assigning them to
-- members, and return resolved email identities from the verified Auth table.

create table if not exists public.docqa_workspace_groups (
    tenant_id text not null references public.docqa_workspaces(tenant_id) on delete cascade,
    name text not null check (
        length(btrim(name)) between 1 and 100
        and position(',' in name) = 0
        and lower(btrim(name)) <> 'all'
    ),
    created_by uuid,
    created_at timestamptz not null default now(),
    primary key (tenant_id, name)
);

alter table public.docqa_workspace_groups enable row level security;
revoke all on public.docqa_workspace_groups from public, anon, authenticated;
grant all on public.docqa_workspace_groups to service_role;

insert into public.docqa_workspace_groups (tenant_id, name)
select distinct m.tenant_id, btrim(group_name)
from public.docqa_workspace_members m
cross join lateral unnest(coalesce(m.groups, array[]::text[])) as g(group_name)
where length(btrim(group_name)) between 1 and 100
  and position(',' in group_name) = 0
  and lower(btrim(group_name)) <> 'all'
on conflict (tenant_id, name) do nothing;

insert into public.docqa_workspace_groups (tenant_id, name)
select distinct d.tenant_id, btrim(group_name)
from public.docqa_documents d
cross join lateral unnest(coalesce(d.acl_groups, array[]::text[])) as g(group_name)
where length(btrim(group_name)) between 1 and 100
  and position(',' in group_name) = 0
  and lower(btrim(group_name)) <> 'all'
on conflict (tenant_id, name) do nothing;

create or replace function public.docqa_workspace_directory(p_tenant_id text)
returns table (
    groups text[],
    members jsonb
)
language sql
stable
security definer
set search_path = pg_catalog, public, auth, pg_temp
as $$
    select
        coalesce(
            array(
                select distinct available.name
                from (
                    select g.name
                    from public.docqa_workspace_groups g
                    where g.tenant_id = p_tenant_id
                    union
                    select btrim(member_group)
                    from public.docqa_workspace_members m
                    cross join lateral unnest(coalesce(m.groups, array[]::text[])) as mg(member_group)
                    where m.tenant_id = p_tenant_id
                      and length(btrim(member_group)) between 1 and 100
                      and position(',' in member_group) = 0
                      and lower(btrim(member_group)) <> 'all'
                ) available
                order by available.name
            ),
            array[]::text[]
        ) as groups,
        coalesce(
            (
                select jsonb_agg(
                    jsonb_build_object(
                        'email', u.email,
                        'groups', coalesce(m.groups, array[]::text[]),
                        'roles', coalesce(m.roles, array[]::text[])
                    )
                    order by m.joined_at, m.user_id
                )
                from public.docqa_workspace_members m
                left join auth.users u on u.id = m.user_id
                where m.tenant_id = p_tenant_id
            ),
            '[]'::jsonb
        ) as members;
$$;

create or replace function public.docqa_create_workspace_group(
    p_tenant_id text,
    p_name text,
    p_created_by uuid
)
returns text
language plpgsql
security definer
set search_path = pg_catalog, public, pg_temp
as $$
declare
    v_name text := btrim(coalesce(p_name, ''));
begin
    if p_tenant_id is null or length(btrim(p_tenant_id)) = 0
       or p_created_by is null
       or length(v_name) < 1 or length(v_name) > 100
       or position(',' in v_name) > 0
       or lower(v_name) = 'all' then
        raise exception 'invalid workspace group';
    end if;

    insert into public.docqa_workspace_groups (tenant_id, name, created_by)
    values (p_tenant_id, v_name, p_created_by)
    on conflict (tenant_id, name) do nothing;
    return v_name;
end;
$$;

revoke all on function public.docqa_workspace_directory(text)
    from public, anon, authenticated;
grant execute on function public.docqa_workspace_directory(text) to service_role;
revoke all on function public.docqa_create_workspace_group(text, text, uuid)
    from public, anon, authenticated;
grant execute on function public.docqa_create_workspace_group(text, text, uuid)
    to service_role;

-- Only failed query execution is an error. Refusals, missing evidence, and
-- grounding rejections are valid product outcomes, not service failures.
create or replace function public.docqa_admin_dashboard(
    p_tenant_id text,
    p_limit integer default 50
)
returns jsonb
language sql
stable
security definer
set search_path = pg_catalog, public, pg_temp
as $$
    select jsonb_build_object(
        'member_count',
            (select count(*)::integer
             from public.docqa_workspace_members m
             where m.tenant_id = p_tenant_id),
        'document_count',
            (select count(*)::integer
             from public.docqa_documents d
             where d.tenant_id = p_tenant_id),
        'query_count',
            (select count(*)::integer
             from public.docqa_audit_events e
            where e.tenant_id = p_tenant_id
              and e.action in ('query', 'query_error')),
        'error_count',
            (select count(*)::integer
             from public.docqa_audit_events e
             where e.tenant_id = p_tenant_id
               and e.action = 'query_error'
               and e.outcome = 'error'),
        'document_activity_count',
            (select count(*)::integer
             from public.docqa_audit_events e
             where e.tenant_id = p_tenant_id
               and e.action in ('ingest', 'delete', 'acl_update')),
        'recent_events',
            coalesce(
                (
                    select jsonb_agg(
                        jsonb_build_object(
                            'id', e.id,
                            'user_id', e.user_id::text,
                            'action', e.action,
                            'document_name', e.document_name,
                            'documents_retrieved', coalesce(e.documents_retrieved, array[]::text[]),
                            'documents_cited', coalesce(e.documents_cited, array[]::text[]),
                            'outcome', e.outcome,
                            'created_at', e.created_at
                        )
                        order by e.created_at desc, e.id desc
                    )
                    from (
                        select *
                        from public.docqa_audit_events
                        where tenant_id = p_tenant_id
                          and action <> 'sign_in'
                        order by created_at desc, id desc
                        limit greatest(1, least(coalesce(p_limit, 50), 200))
                    ) e
                ),
                '[]'::jsonb
            )
    );
$$;

revoke all on function public.docqa_admin_dashboard(text, integer)
    from public, anon, authenticated;
grant execute on function public.docqa_admin_dashboard(text, integer)
    to service_role;
