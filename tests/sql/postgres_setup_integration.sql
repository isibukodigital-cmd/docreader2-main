begin;

do $portable_setup$
declare
    v_user_id uuid := gen_random_uuid();
    v_workspace record;
begin
    if to_regnamespace('auth') is not null then
        raise exception 'generic PostgreSQL setup retained the temporary auth schema';
    end if;

    if not exists (
        select 1
        from pg_extension e
        join pg_namespace n on n.oid = e.extnamespace
        where e.extname = 'vector'
          and n.nspname = 'extensions'
    ) then
        raise exception 'pgvector was not installed in the expected schema';
    end if;

    if exists (
        select 1
        from pg_proc p
        join pg_namespace n on n.oid = p.pronamespace
        where n.nspname = 'public'
          and p.prosrc like '%auth.users%'
    ) then
        raise exception 'generic PostgreSQL functions still depend on auth.users';
    end if;

    perform public.docqa_ensure_identity(
        v_user_id,
        'cognito',
        'https://cognito-idp.eu-north-1.amazonaws.com/example',
        'example-subject',
        'portable-setup@example.invalid',
        true
    );

    perform public.docqa_update_identity_app_metadata(
        v_user_id,
        jsonb_build_object('tenant_id', 'portable-setup-test', 'groups', jsonb_build_array('owner'))
    );

    if public.docqa_get_identity_app_metadata(v_user_id) ->> 'tenant_id'
       <> 'portable-setup-test' then
        raise exception 'provider-neutral identity metadata RPCs are unavailable';
    end if;

    select * into v_workspace
    from public.docqa_provision_trial_workspace(
        v_user_id,
        'cognito',
        'portable-setup-' || v_user_id::text,
        'personal',
        'Portable setup test',
        10
    );
    if v_workspace.workspace_type <> 'personal'
       or v_workspace.member_count <> 1 then
        raise exception 'workspace RPCs are unavailable after generic PostgreSQL setup';
    end if;
end
$portable_setup$;

rollback;
