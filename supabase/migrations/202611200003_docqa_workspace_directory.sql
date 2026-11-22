-- Verified workspace-directory data for the document ACL picker.
-- Individual identities remain out of scope: the API exposes registered UUIDs,
-- groups, and roles only to workspace administrators.

create or replace function public.docqa_workspace_directory(p_tenant_id text)
returns table (
    groups text[],
    members jsonb
)
language sql
stable
security definer
set search_path = pg_catalog, public, pg_temp
as $$
    select
        coalesce(
            array(
                select distinct group_name
                from public.docqa_workspace_members m
                cross join lateral unnest(coalesce(m.groups, array[]::text[])) group_name
                where m.tenant_id = p_tenant_id
                  and btrim(group_name) <> ''
                order by group_name
            ),
            array[]::text[]
        ) as groups,
        coalesce(
            (
                select jsonb_agg(
                    jsonb_build_object(
                        'user_id', m.user_id::text,
                        'groups', coalesce(m.groups, array[]::text[]),
                        'roles', coalesce(m.roles, array[]::text[])
                    )
                    order by m.joined_at, m.user_id
                )
                from public.docqa_workspace_members m
                where m.tenant_id = p_tenant_id
            ),
            '[]'::jsonb
        ) as members;
$$;

revoke all on function public.docqa_workspace_directory(text) from public, anon, authenticated;
grant execute on function public.docqa_workspace_directory(text) to service_role;
