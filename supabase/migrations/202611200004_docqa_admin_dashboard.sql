-- Tenant-scoped organization administration and audit metadata.
-- Raw question text is intentionally excluded from dashboard output.

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
             where e.tenant_id = p_tenant_id and e.action = 'query'),
        'error_count',
            (select count(*)::integer
             from public.docqa_audit_events e
             where e.tenant_id = p_tenant_id
               and e.action = 'query'
               and e.outcome is not null
               and e.outcome <> 'ok'),
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
