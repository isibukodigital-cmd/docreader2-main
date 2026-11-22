-- pgvector's HNSW implementation normally applies post-filtering. Enable
-- iterative scanning so tenant/ACL filtering can continue beyond nearby rows
-- that belong to other tenants or restricted documents.
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
language plpgsql
stable
security definer
set search_path = pg_catalog, public, extensions, pg_temp
as $$
begin
    perform set_config(
        'hnsw.iterative_scan',
        'strict_order',
        true
    );
    perform set_config(
        'hnsw.max_scan_tuples',
        greatest(20000, p_candidate_count * 50, p_match_count * 50)::text,
        true
    );

    return query
    with q as (
        select to_tsquery('simple', string_agg(quote_literal(w), ' | ')) as terms
        from unnest(tsvector_to_array(to_tsvector('simple', coalesce(p_query, '')))) as w
        where length(w) > 1
          and w <> all (array[
              'a','an','the','and','or','but','if','of','to','in','on','at','by','for',
              'with','from','as','is','are','was','were','be','been','being','am',
              'do','does','did','done','has','have','had','having','it','its','this',
              'that','these','those','what','which','who','whom','whose','when','where',
              'why','how','can','could','should','would','will','shall','may','might',
              'must','i','me','my','we','our','you','your','they','their','them','he',
              'she','his','her','not','no','than','then','there','about','into','any',
              'some','so','such','also','tell','please'
          ])
    ),
    eligible as not materialized (
        select c.*, d.acl_groups, q.terms as query_terms
        from public.docqa_chunks c
        join public.docqa_documents d
          on d.id = c.document_id
         and d.tenant_id = c.tenant_id
        cross join q
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
        where e.query_terms is not null
          and e.search_tsv @@ e.query_terms
        order by ts_rank_cd(e.search_tsv, e.query_terms) desc, e.id
        limit greatest(p_candidate_count, p_match_count)
    ),
    semantic as (
        select e.id, row_number() over (
            order by e.embedding <=> p_query_embedding, e.id
        ) as rank
        from eligible e
        where (e.embedding <=> p_query_embedding) <= 0.6
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
           coalesce(ts_rank_cd(e.search_tsv, e.query_terms), 0)::double precision,
           (1 - (e.embedding <=> p_query_embedding))::double precision
    from fused f
    join eligible e on e.id = f.id
    order by f.score desc, e.id
    limit greatest(1, least(p_match_count, 20));
end;
$$;

revoke all on function public.docqa_search_chunks(
    text, text[], text, extensions.vector, integer, integer, integer
) from public, anon, authenticated;
grant execute on function public.docqa_search_chunks(
    text, text[], text, extensions.vector, integer, integer, integer
) to service_role;
