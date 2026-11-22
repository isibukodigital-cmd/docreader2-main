create or replace function public.docqa_list_documents(p_tenant_id text)
returns table (
    document_id uuid,
    name text,
    acl_groups text[],
    uploaded_by uuid,
    created_at timestamptz,
    chunk_count bigint
)
language sql
stable
security definer
set search_path = pg_catalog, public, extensions, pg_temp
as $$
    select d.id,
           d.name,
           d.acl_groups,
           d.uploaded_by,
           d.created_at,
           count(c.id)::bigint
    from public.docqa_documents d
    left join public.docqa_chunks c
      on c.document_id = d.id
     and c.tenant_id = d.tenant_id
    where d.tenant_id = p_tenant_id
    group by d.id
    order by d.created_at desc, d.name;
$$;

create or replace function public.docqa_update_document_acl(
    p_tenant_id text,
    p_document_id uuid,
    p_acl_groups text[]
)
returns boolean
language plpgsql
security definer
set search_path = pg_catalog, public, extensions, pg_temp
as $$
begin
    if p_tenant_id is null or length(trim(p_tenant_id)) = 0 then
        raise exception 'tenant_id is required';
    end if;
    if coalesce(array_length(p_acl_groups, 1), 0) = 0
       or array_length(p_acl_groups, 1) > 50
       or exists (
           select 1
           from unnest(p_acl_groups) as groups(group_name)
           where group_name is null
              or length(trim(group_name)) = 0
              or length(group_name) > 100
       ) then
        raise exception 'invalid ACL groups';
    end if;

    update public.docqa_documents
       set acl_groups = p_acl_groups
     where tenant_id = p_tenant_id
       and id = p_document_id;
    return found;
end;
$$;

revoke all on function public.docqa_list_documents(text)
    from public, anon, authenticated;
revoke all on function public.docqa_update_document_acl(text, uuid, text[])
    from public, anon, authenticated;

grant execute on function public.docqa_list_documents(text)
    to service_role;
grant execute on function public.docqa_update_document_acl(text, uuid, text[])
    to service_role;
