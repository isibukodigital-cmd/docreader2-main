create or replace function public.docqa_sync_identity_metadata_to_supabase_auth()
returns trigger
language plpgsql
security definer
set search_path = pg_catalog, public, auth, pg_temp
as $$
declare
    v_metadata_changed boolean;
begin
    if tg_op = 'INSERT' then
        v_metadata_changed := true;
    else
        v_metadata_changed := new.app_metadata is distinct from old.app_metadata;
    end if;

    if new.auth_provider = 'supabase' and v_metadata_changed then
        update auth.users u
           set raw_app_meta_data = new.app_metadata
         where u.id = new.user_id
           and u.raw_app_meta_data is distinct from new.app_metadata;
    end if;
    return new;
end;
$$;

revoke all on function public.docqa_sync_identity_metadata_to_supabase_auth()
    from public, anon, authenticated, service_role;

drop trigger if exists docqa_sync_identity_metadata on public.docqa_identity_users;
create trigger docqa_sync_identity_metadata
after insert or update on public.docqa_identity_users
for each row execute function public.docqa_sync_identity_metadata_to_supabase_auth();

create or replace function public.docqa_update_identity_app_metadata(
    p_user_id uuid,
    p_app_metadata jsonb
)
returns void
language plpgsql
security definer
set search_path = pg_catalog, public, pg_temp
as $$
begin
    if p_user_id is null or p_app_metadata is null
       or jsonb_typeof(p_app_metadata) <> 'object' then
        raise exception 'invalid identity metadata update';
    end if;

    update public.docqa_identity_users
       set app_metadata = p_app_metadata,
           updated_at = now()
     where user_id = p_user_id;
    if not found then
        raise exception 'identity user not found';
    end if;
end;
$$;

revoke all on function public.docqa_update_identity_app_metadata(uuid, jsonb)
    from public, anon, authenticated;
grant execute on function public.docqa_update_identity_app_metadata(uuid, jsonb)
    to service_role;
