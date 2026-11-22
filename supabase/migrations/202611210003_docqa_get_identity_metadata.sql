create or replace function public.docqa_get_identity_app_metadata(p_user_id uuid)
returns jsonb
language sql
stable
security definer
set search_path = pg_catalog, public, pg_temp
as $$
    select i.app_metadata
    from public.docqa_identity_users i
    where i.user_id = p_user_id;
$$;

revoke all on function public.docqa_get_identity_app_metadata(uuid)
    from public, anon, authenticated;
grant execute on function public.docqa_get_identity_app_metadata(uuid)
    to service_role;
