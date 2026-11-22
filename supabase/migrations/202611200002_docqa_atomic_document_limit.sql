-- Enforce the trial document limit inside the ingest transaction. The API previously
-- counted documents and then inserted, so concurrent uploads could exceed the limit.
-- A per-tenant advisory transaction lock serialises ingests for the same tenant.

drop function if exists public.docqa_ingest_document(text, text, text[], uuid, jsonb);

create or replace function public.docqa_ingest_document(
    p_tenant_id text,
    p_name text,
    p_acl_groups text[],
    p_uploaded_by uuid,
    p_chunks jsonb,
    p_document_limit integer default null
)
returns uuid
language plpgsql
security definer
set search_path = pg_catalog, public, extensions, pg_temp
as $$
declare
    v_document_id uuid;
    v_chunk jsonb;
begin
    if p_tenant_id is null or length(trim(p_tenant_id)) = 0 then
        raise exception 'tenant_id is required';
    end if;
    if p_name is null or length(trim(p_name)) = 0 then
        raise exception 'document name is required';
    end if;
    if coalesce(array_length(p_acl_groups, 1), 0) = 0 then
        raise exception 'at least one ACL group is required';
    end if;
    if jsonb_typeof(p_chunks) <> 'array' or jsonb_array_length(p_chunks) = 0 then
        raise exception 'at least one chunk is required';
    end if;

    perform pg_advisory_xact_lock(hashtextextended('docqa_ingest:' || p_tenant_id, 0));

    if p_document_limit is not null
       and not exists (
           select 1 from public.docqa_documents d
           where d.tenant_id = p_tenant_id and d.name = p_name
       )
       and (select count(*) from public.docqa_documents d where d.tenant_id = p_tenant_id)
           >= p_document_limit then
        raise exception 'trial document limit reached';
    end if;

    insert into public.docqa_documents (tenant_id, name, acl_groups, uploaded_by)
    values (p_tenant_id, p_name, p_acl_groups, p_uploaded_by)
    on conflict (tenant_id, name) do update
      set acl_groups = excluded.acl_groups,
          uploaded_by = excluded.uploaded_by,
          created_at = now()
    returning id into v_document_id;

    delete from public.docqa_chunks where document_id = v_document_id;

    for v_chunk in select value from jsonb_array_elements(p_chunks)
    loop
        insert into public.docqa_chunks (
            tenant_id, document_id, document_name, location, content, embedding
        )
        values (
            p_tenant_id,
            v_document_id,
            p_name,
            v_chunk ->> 'location',
            v_chunk ->> 'content',
            (v_chunk -> 'embedding')::text::extensions.vector(384)
        );
    end loop;

    return v_document_id;
end;
$$;

revoke all on function public.docqa_ingest_document(text, text, text[], uuid, jsonb, integer)
    from public, anon, authenticated;
grant execute on function public.docqa_ingest_document(text, text, text[], uuid, jsonb, integer)
    to service_role;
