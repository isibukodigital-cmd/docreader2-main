create extension if not exists vector with schema extensions;

create table if not exists public.docqa_documents (
    id uuid primary key default gen_random_uuid(),
    tenant_id text not null,
    name text not null,
    acl_groups text[] not null default array['admin']::text[],
    uploaded_by uuid,
    created_at timestamptz not null default now(),
    unique (tenant_id, name)
);

create table if not exists public.docqa_chunks (
    id uuid primary key default gen_random_uuid(),
    tenant_id text not null,
    document_id uuid not null references public.docqa_documents(id) on delete cascade,
    document_name text not null,
    location text not null,
    content text not null,
    embedding extensions.vector(384) not null,
    search_tsv tsvector generated always as (
        setweight(to_tsvector('simple', document_name), 'A') ||
        setweight(to_tsvector('simple', content), 'B')
    ) stored
);

create index if not exists docqa_chunks_tenant_idx
    on public.docqa_chunks (tenant_id);
create index if not exists docqa_documents_acl_idx
    on public.docqa_documents using gin (acl_groups);
create index if not exists docqa_chunks_search_idx
    on public.docqa_chunks using gin (search_tsv);
create index if not exists docqa_chunks_embedding_idx
    on public.docqa_chunks using hnsw (embedding extensions.vector_cosine_ops);

create table if not exists public.docqa_audit_events (
    id bigint generated always as identity primary key,
    tenant_id text not null,
    user_id uuid not null,
    action text not null,
    question text,
    document_name text,
    documents_retrieved text[] not null default array[]::text[],
    documents_cited text[] not null default array[]::text[],
    outcome text,
    created_at timestamptz not null default now()
);

alter table public.docqa_documents enable row level security;
alter table public.docqa_chunks enable row level security;
alter table public.docqa_audit_events enable row level security;

revoke all on public.docqa_documents, public.docqa_chunks, public.docqa_audit_events
    from public, anon, authenticated;
grant all on public.docqa_documents, public.docqa_chunks, public.docqa_audit_events
    to service_role;
grant usage, select on sequence public.docqa_audit_events_id_seq to service_role;

create or replace function public.docqa_ingest_document(
    p_tenant_id text,
    p_name text,
    p_acl_groups text[],
    p_uploaded_by uuid,
    p_chunks jsonb
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

create or replace function public.docqa_delete_document(
    p_tenant_id text,
    p_document_id uuid
)
returns boolean
language sql
security definer
set search_path = pg_catalog, public, extensions, pg_temp
as $$
    with deleted as (
        delete from public.docqa_documents
        where tenant_id = p_tenant_id and id = p_document_id
        returning id
    )
    select exists(select 1 from deleted);
$$;

create or replace function public.docqa_search_chunks(
    p_tenant_id text,
    p_user_groups text[],
    p_query text,
    p_query_embedding extensions.vector(384),
    p_match_count integer default 4,
    p_candidate_count integer default 30,
    p_rrf_k integer default 60
)
returns table (
    chunk_id uuid,
    document_id uuid,
    document_name text,
    location text,
    content text,
    acl_groups text[],
    score double precision,
    lexical_score double precision,
    semantic_score double precision
)
language sql
stable
security definer
set search_path = pg_catalog, public, extensions, pg_temp
as $$
    with eligible as materialized (
        select c.*, d.acl_groups,
               plainto_tsquery('simple', p_query) as query_terms
        from public.docqa_chunks c
        join public.docqa_documents d
          on d.id = c.document_id
         and d.tenant_id = c.tenant_id
        where c.tenant_id = p_tenant_id
          and (
              'all' = any(d.acl_groups)
              or coalesce(d.acl_groups, array[]::text[])
                    && coalesce(p_user_groups, array[]::text[])
          )
    ),
    lexical as (
        select e.id, row_number() over (
            order by ts_rank_cd(e.search_tsv, e.query_terms) desc, e.id
        ) as rank
        from eligible e
        where e.search_tsv @@ e.query_terms
        order by ts_rank_cd(e.search_tsv, e.query_terms) desc, e.id
        limit greatest(p_candidate_count, p_match_count)
    ),
    semantic as (
        select e.id, row_number() over (
            order by e.embedding <=> p_query_embedding, e.id
        ) as rank
        from eligible e
        where (e.embedding <=> p_query_embedding) < 1
        order by e.embedding <=> p_query_embedding, e.id
        limit greatest(p_candidate_count, p_match_count)
    ),
    fused as (
        select r.id, sum(1.0 / (p_rrf_k + r.rank))::double precision as score
        from (
            select id, rank from lexical
            union all
            select id, rank from semantic
        ) r
        group by r.id
    )
    select e.id, e.document_id, e.document_name, e.location, e.content,
           e.acl_groups, f.score,
           ts_rank_cd(e.search_tsv, e.query_terms)::double precision,
           (1 - (e.embedding <=> p_query_embedding))::double precision
    from fused f
    join eligible e on e.id = f.id
    order by f.score desc, e.id
    limit greatest(1, least(p_match_count, 20));
$$;

revoke all on function public.docqa_ingest_document(text, text, text[], uuid, jsonb)
    from public, anon, authenticated;
revoke all on function public.docqa_delete_document(text, uuid)
    from public, anon, authenticated;
revoke all on function public.docqa_search_chunks(
    text, text[], text, extensions.vector, integer, integer, integer
) from public, anon, authenticated;

grant execute on function public.docqa_ingest_document(text, text, text[], uuid, jsonb)
    to service_role;
grant execute on function public.docqa_delete_document(text, uuid)
    to service_role;
grant execute on function public.docqa_search_chunks(
    text, text[], text, extensions.vector, integer, integer, integer
) to service_role;
