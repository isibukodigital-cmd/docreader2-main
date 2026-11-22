do $migration$
declare
    v_function_names text[] := array[
        'docqa_workspace_directory',
        'docqa_list_platform_admins',
        'docqa_list_platform_admin_audit',
        'docqa_grant_platform_admin',
        'docqa_provision_trial_workspace',
        'docqa_create_organization_request',
        'docqa_export_workspace',
        'docqa_list_workspace_offboarding_requests',
        'docqa_review_workspace_offboarding_request'
    ];
    v_function record;
    v_definition text;
    v_function_count integer;
    v_foreign_key record;
    v_foreign_key_count integer := 0;
begin
    select count(*) into v_function_count
    from pg_proc p
    join pg_namespace n on n.oid = p.pronamespace
    where n.nspname = 'public'
      and p.proname = any(v_function_names)
      and p.prosrc like '%auth.users%';
    if v_function_count <> cardinality(v_function_names) then
        raise exception 'expected % Supabase-dependent business functions, found %',
            cardinality(v_function_names), v_function_count;
    end if;

    -- Rewrite only the known business functions; fail if an expected dependency remains.
    for v_function in
        select p.oid, pg_get_functiondef(p.oid) as definition
        from pg_proc p
        join pg_namespace n on n.oid = p.pronamespace
        where n.nspname = 'public'
          and p.proname = any(v_function_names)
          and p.prosrc like '%auth.users%'
    loop
        v_definition := replace(v_function.definition,
            'auth.users', 'public.docqa_identity_users');
        v_definition := replace(v_definition, 'u.id', 'u.user_id');
        v_definition := replace(v_definition, 'actor.id', 'actor.user_id');
        v_definition := replace(v_definition, 'target.id', 'target.user_id');
        v_definition := replace(
            v_definition,
            'u.email_confirmed_at is not null',
            'u.email_verified'
        );
        v_definition := replace(v_definition, 'raw_app_meta_data', 'app_metadata');

        if position('auth.users' in v_definition) > 0
           or position('email_confirmed_at' in v_definition) > 0
           or position('raw_app_meta_data' in v_definition) > 0 then
            raise exception 'failed to make function % identity-provider-neutral',
                v_function.oid::regprocedure;
        end if;
        execute v_definition;
    end loop;

    for v_foreign_key in
        select c.conrelid::regclass as relation,
               c.conname,
               pg_get_constraintdef(c.oid) as definition
        from pg_constraint c
        join pg_class t on t.oid = c.conrelid
        join pg_namespace n on n.oid = t.relnamespace
        where c.contype = 'f'
          and c.confrelid = 'auth.users'::regclass
          and n.nspname = 'public'
    loop
        if position('REFERENCES auth.users(id)' in v_foreign_key.definition) = 0 then
            raise exception 'unexpected Auth foreign key definition on %.%',
                v_foreign_key.relation, v_foreign_key.conname;
        end if;

        execute format(
            'alter table %s drop constraint %I',
            v_foreign_key.relation,
            v_foreign_key.conname
        );
        execute format(
            'alter table %s add constraint %I %s',
            v_foreign_key.relation,
            v_foreign_key.conname,
            replace(
                v_foreign_key.definition,
                'REFERENCES auth.users(id)',
                'REFERENCES public.docqa_identity_users(user_id)'
            )
        );
        v_foreign_key_count := v_foreign_key_count + 1;
    end loop;

    if v_foreign_key_count <> 7 then
        raise exception 'expected 7 Auth foreign keys, migrated %', v_foreign_key_count;
    end if;
end;
$migration$;
