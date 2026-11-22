begin;

do $docqa_integration$
declare
    v_owner uuid := gen_random_uuid();
    v_member uuid := gen_random_uuid();
    v_platform_admin uuid;
    v_platform_admin_count bigint;
    v_unrelated_user uuid := gen_random_uuid();
    v_microsoft_tenant uuid := gen_random_uuid();
    v_manual_tenant uuid := gen_random_uuid();
    v_unregistered_tenant uuid := gen_random_uuid();
    v_request_id uuid := gen_random_uuid();
    v_group_id uuid := gen_random_uuid();
    v_workspace_tenant text;
    v_limit_tenant text := 'integration-limit-' || gen_random_uuid()::text;
    v_other_tenant text := 'integration-other-' || gen_random_uuid()::text;
    v_recall_tenant text := 'integration-recall-' || gen_random_uuid()::text;
    v_recall_other text := 'integration-recall-other-' || gen_random_uuid()::text;
    v_selected_groups jsonb;
    v_embedding jsonb := to_jsonb(array_fill(0::real, array[384]));
    v_vector extensions.vector(384);
    v_weak_vector extensions.vector(384);
    v_request record;
    v_review record;
    v_registered_organization record;
    v_platform_admin_result record;
    v_owner_workspace record;
    v_member_workspace record;
    v_offboarding jsonb;
    v_document_id uuid;
    v_hit_count bigint;
    v_hit_name text;
    v_count bigint;
    v_expected_error boolean;
    v_index integer;
    v_explain_line record;
    v_plan text := '';
    v_seqscan_setting text;
    v_bitmapscan_setting text;
begin
    if exists (
        select 1
        from pg_proc p
        join pg_namespace n on n.oid = p.pronamespace
        where n.nspname = 'public'
          and p.proname = any(array[
              'docqa_workspace_directory',
              'docqa_list_platform_admins',
              'docqa_list_platform_admin_audit',
              'docqa_grant_platform_admin',
              'docqa_provision_trial_workspace',
              'docqa_create_organization_request',
              'docqa_export_workspace',
              'docqa_list_workspace_offboarding_requests',
              'docqa_review_workspace_offboarding_request'
          ])
          and p.prosrc like '%auth.users%'
    ) then
        raise exception 'application SQL functions still depend on Supabase Auth users';
    end if;

    if exists (
        select 1
        from pg_constraint c
        join pg_class t on t.oid = c.conrelid
        join pg_namespace n on n.oid = t.relnamespace
        where c.contype = 'f'
          and c.confrelid = 'auth.users'::regclass
          and n.nspname = 'public'
    ) then
        raise exception 'application foreign keys still depend on Supabase Auth users';
    end if;

    select count(*) into v_platform_admin_count
    from public.docqa_platform_admins;
    if v_platform_admin_count > 1 then
        raise exception 'SQL integration test requires at most one existing platform administrator; use a fresh disposable database';
    elsif v_platform_admin_count = 1 then
        select a.user_id into v_platform_admin
        from public.docqa_platform_admins a
        order by a.created_at, a.user_id
        limit 1;
    else
        v_platform_admin := gen_random_uuid();
    end if;

    insert into auth.users (
        id, email, email_confirmed_at, raw_app_meta_data, raw_user_meta_data
    )
    values
        (v_owner, 'owner-' || v_owner || '@integration.example', now(), '{}'::jsonb, '{}'::jsonb),
        (v_member, 'member-' || v_member || '@integration.example', now(), '{}'::jsonb, '{}'::jsonb),
        (v_unrelated_user, 'unrelated-' || v_unrelated_user || '@integration.example', now(), '{}'::jsonb, '{}'::jsonb);

    if not exists (
        select 1
        from public.docqa_identity_users i
        where i.user_id = v_owner
          and i.auth_provider = 'supabase'
          and i.issuer = 'supabase'
          and i.subject = v_owner::text
          and i.email = 'owner-' || v_owner || '@integration.example'
          and i.email_verified
    ) then
        raise exception 'Supabase Auth user was not mirrored to the provider-neutral identity registry';
    end if;

    update auth.users
       set raw_app_meta_data = jsonb_build_object('provider', 'google', 'tenant_id', 'identity-sync-test')
     where id = v_owner;
    if not exists (
        select 1
        from public.docqa_identity_users i
        where i.user_id = v_owner
          and i.app_metadata ->> 'tenant_id' = 'identity-sync-test'
    ) then
        raise exception 'Supabase Auth metadata was not synchronized to the identity registry';
    end if;

    perform public.docqa_update_identity_app_metadata(
        v_owner,
        jsonb_build_object('provider', 'google', 'tenant_id', 'identity-write-test')
    );
    if not exists (
        select 1
        from auth.users u
        join public.docqa_identity_users i on i.user_id = u.id
        where u.id = v_owner
          and u.raw_app_meta_data ->> 'tenant_id' = 'identity-write-test'
          and i.app_metadata ->> 'tenant_id' = 'identity-write-test'
    ) then
        raise exception 'application identity metadata update did not synchronize to Supabase Auth';
    end if;
    if public.docqa_get_identity_app_metadata(v_owner) ->> 'tenant_id'
       <> 'identity-write-test' then
        raise exception 'identity metadata lookup did not return application-owned metadata';
    end if;

    if v_platform_admin_count = 0 then
        insert into auth.users (
            id, email, email_confirmed_at, raw_app_meta_data, raw_user_meta_data
        )
        values (
            v_platform_admin,
            'platform-' || v_platform_admin || '@integration.example',
            now(),
            '{}'::jsonb,
            '{}'::jsonb
        );
        insert into public.docqa_platform_admins (user_id, granted_by)
        values (v_platform_admin, v_platform_admin);
    end if;

    select * into v_platform_admin_result
    from public.docqa_grant_platform_admin(
        v_platform_admin,
        'unrelated-' || v_unrelated_user || '@integration.example'
    );
    if v_platform_admin_result.user_id <> v_unrelated_user then
        raise exception 'platform-admin grant did not resolve the confirmed target';
    end if;
    perform public.docqa_revoke_platform_admin(v_platform_admin, v_unrelated_user);

    select * into v_registered_organization
    from public.docqa_set_registered_organization(
        v_manual_tenant,
        'Manual Registration',
        array['manual@example.com']::text[]
    );
    select * into v_registered_organization
    from public.docqa_set_registered_organization(
        v_manual_tenant,
        'Updated Manual Registration',
        array['updated-manual@example.com']::text[]
    );
    if v_registered_organization.name <> 'Updated Manual Registration' then
        raise exception 'registered organization upsert did not update the existing row';
    end if;
    perform public.docqa_remove_registered_organization(v_manual_tenant);

    v_selected_groups := jsonb_build_array(jsonb_build_object(
        'group_id', v_group_id::text,
        'display_name', 'Finance',
        'workspace_group_name', 'Finance'
    ));

    select * into v_request
    from public.docqa_create_organization_request(
        v_request_id,
        v_microsoft_tenant,
        'Integration Organization',
        array['integration.example']::text[],
        v_owner,
        v_selected_groups
    );
    if v_request.status <> 'pending'
       or v_request.requester_email <> ('owner-' || v_owner || '@integration.example') then
        raise exception 'organization request did not preserve the confirmed requester';
    end if;

    select * into v_request
    from public.docqa_create_organization_request(
        gen_random_uuid(),
        v_microsoft_tenant,
        'Updated Integration Organization',
        array['updated.integration.example']::text[],
        v_owner,
        v_selected_groups
    );
    if v_request.request_id <> v_request_id
       or v_request.name <> 'Updated Integration Organization'
       or v_request.status <> 'pending' then
        raise exception 'pending organization request was not updated in place';
    end if;

    v_expected_error := false;
    begin
        perform public.docqa_review_organization_request(
            v_request_id, v_unrelated_user, true, 'not authorized'
        );
    exception when others then
        if sqlerrm = 'platform administrator role is required' then
            v_expected_error := true;
        else
            raise;
        end if;
    end;
    if not v_expected_error then
        raise exception 'non-platform-admin organization approval unexpectedly succeeded';
    end if;

    select * into v_review
    from public.docqa_review_organization_request(
        v_request_id, v_platform_admin, true, 'SQL integration approval'
    );
    if v_review.status <> 'approved' then
        raise exception 'platform-admin approval did not approve the request';
    end if;

    v_expected_error := false;
    begin
        perform public.docqa_create_organization_request(
            gen_random_uuid(),
            v_microsoft_tenant,
            'Duplicate Organization',
            array['integration.example']::text[],
            v_owner,
            v_selected_groups
        );
    exception when others then
        if sqlerrm = 'organization already registered' then
            v_expected_error := true;
        else
            raise;
        end if;
    end;
    if not v_expected_error then
        raise exception 'duplicate organization registration did not return a clean error';
    end if;

    select * into v_owner_workspace
    from public.docqa_provision_trial_workspace(
        v_owner, 'azure', v_microsoft_tenant::text, 'organization', 'ignored', 15
    );
    v_workspace_tenant := v_owner_workspace.tenant_id;
    if not ('admin' = any(v_owner_workspace.member_roles)) then
        raise exception 'the confirmed initial organization user did not become admin';
    end if;

    select * into v_member_workspace
    from public.docqa_provision_trial_workspace(
        v_member, 'azure', v_microsoft_tenant::text, 'organization', 'ignored', 15
    );
    if v_member_workspace.tenant_id <> v_workspace_tenant
       or 'admin' = any(v_member_workspace.member_roles)
       or not ('staff' = any(v_member_workspace.member_groups)) then
        raise exception 'the next organization user did not become staff in the shared workspace';
    end if;

    v_expected_error := false;
    begin
        perform public.docqa_provision_trial_workspace(
            v_owner, 'azure', v_unregistered_tenant::text, 'organization', 'Unregistered', 15
        );
    exception when others then
        if sqlerrm = 'Microsoft organization is not registered' then
            v_expected_error := true;
        else
            raise;
        end if;
    end;
    if not v_expected_error then
        raise exception 'an unregistered organization unexpectedly provisioned a workspace';
    end if;

    v_expected_error := false;
    begin
        perform public.docqa_revoke_platform_admin(v_platform_admin, v_platform_admin);
    exception when others then
        if sqlerrm = 'cannot remove the last platform administrator' then
            v_expected_error := true;
        else
            raise;
        end if;
    end;
    if not v_expected_error then
        raise exception 'the last platform administrator was unexpectedly revoked';
    end if;

    for v_index in 1..5 loop
        perform public.docqa_ingest_document(
            v_limit_tenant,
            'limit-' || v_index || '.txt',
            array['staff']::text[],
            v_owner,
            jsonb_build_array(jsonb_build_object(
                'location', 'section 1',
                'content', 'Trial document limit check',
                'embedding', v_embedding
            )),
            5
        );
    end loop;

    v_expected_error := false;
    begin
        perform public.docqa_ingest_document(
            v_limit_tenant,
            'limit-6.txt',
            array['staff']::text[],
            v_owner,
            jsonb_build_array(jsonb_build_object(
                'location', 'section 1',
                'content', 'The sixth document must be rejected',
                'embedding', v_embedding
            )),
            5
        );
    exception when others then
        if sqlerrm = 'trial document limit reached' then
            v_expected_error := true;
        else
            raise;
        end if;
    end;
    if not v_expected_error then
        raise exception 'the sixth new document unexpectedly passed the limit';
    end if;

    perform public.docqa_ingest_document(
        v_limit_tenant,
        'limit-1.txt',
        array['staff']::text[],
        v_owner,
        jsonb_build_array(jsonb_build_object(
            'location', 'section 1',
            'content', 'Replacement at the document limit succeeds',
            'embedding', v_embedding
        )),
        5
    );
    select count(*) into v_count
    from public.docqa_documents d
    where d.tenant_id = v_limit_tenant;
    if v_count <> 5 then
        raise exception 'replacing an existing document changed the document count';
    end if;

    v_vector := ('[1' || repeat(',0', 383) || ']')::extensions.vector(384);
    v_weak_vector := (
        '[0.35,' || sqrt(1 - power(0.35::double precision, 2))::text ||
        repeat(',0', 382) || ']'
    )::extensions.vector(384);
    insert into public.docqa_documents (tenant_id, name, acl_groups, uploaded_by)
    values (v_workspace_tenant, 'invoice-retention.txt', array['staff'], v_owner)
    returning id into v_document_id;
    insert into public.docqa_chunks (
        tenant_id, document_id, document_name, location, content, embedding
    )
    values (
        v_workspace_tenant, v_document_id, 'invoice-retention.txt', 'section 1',
        'The retention period for invoices is seven years.', v_vector
    );

    insert into public.docqa_documents (tenant_id, name, acl_groups, uploaded_by)
    values (v_workspace_tenant, 'unrelated-weather.txt', array['staff'], v_owner)
    returning id into v_document_id;
    insert into public.docqa_chunks (
        tenant_id, document_id, document_name, location, content, embedding
    )
    values (
        v_workspace_tenant, v_document_id, 'unrelated-weather.txt', 'section 1',
        'The weather outlook is cloudy in the southern district.', v_weak_vector
    );

    insert into public.docqa_documents (tenant_id, name, acl_groups, uploaded_by)
    values (v_workspace_tenant, 'finance-invoice.txt', array['Finance'], v_owner)
    returning id into v_document_id;
    insert into public.docqa_chunks (
        tenant_id, document_id, document_name, location, content, embedding
    )
    values (
        v_workspace_tenant, v_document_id, 'finance-invoice.txt', 'section 1',
        'The retention period for invoices is ten years.', v_vector
    );

    insert into public.docqa_documents (tenant_id, name, acl_groups, uploaded_by)
    values (v_other_tenant, 'other-tenant-invoice.txt', array['staff'], v_member)
    returning id into v_document_id;
    insert into public.docqa_chunks (
        tenant_id, document_id, document_name, location, content, embedding
    )
    values (
        v_other_tenant, v_document_id, 'other-tenant-invoice.txt', 'section 1',
        'The retention period for invoices is one year.', v_vector
    );

    v_seqscan_setting := current_setting('enable_seqscan');
    v_bitmapscan_setting := current_setting('enable_bitmapscan');
    perform set_config('enable_seqscan', 'off', true);
    perform set_config('enable_bitmapscan', 'off', true);
    for v_explain_line in execute format($explain$
        explain (costs off)
        with eligible as not materialized (
            select c.id, c.embedding
            from public.docqa_chunks c
            join public.docqa_documents d
              on d.id = c.document_id
             and d.tenant_id = c.tenant_id
            where c.tenant_id = %1$L
              and (
                  'all' = any(d.acl_groups)
                  or coalesce(d.acl_groups, array[]::text[]) && array['staff']::text[]
              )
        )
        select e.id, row_number() over (
            order by e.embedding OPERATOR(extensions.<=>) %2$L::extensions.vector(384)
        ) as rank
        from eligible e
        where (e.embedding OPERATOR(extensions.<=>) %2$L::extensions.vector(384)) <= 0.6
        order by e.embedding OPERATOR(extensions.<=>) %2$L::extensions.vector(384)
        limit 30
    $explain$, v_workspace_tenant, v_vector::text) loop
        v_plan := v_plan || v_explain_line."QUERY PLAN" || E'\n';
    end loop;
    perform set_config('enable_seqscan', v_seqscan_setting, true);
    perform set_config('enable_bitmapscan', v_bitmapscan_setting, true);
    if position('docqa_chunks_embedding_idx' in v_plan) = 0 then
        raise exception 'semantic candidate plan did not use the HNSW index: %', v_plan;
    end if;

    insert into public.docqa_documents (tenant_id, name, acl_groups, uploaded_by)
    values (v_recall_tenant, 'recall-target.txt', array['all'], v_owner)
    returning id into v_document_id;
    insert into public.docqa_chunks (
        tenant_id, document_id, document_name, location, content, embedding
    )
    values (
        v_recall_tenant, v_document_id, 'recall-target.txt', 'section 1',
        'The internal vector is Alpha Gamma Seven.', v_vector
    );

    for v_index in 1..10000 loop
        insert into public.docqa_documents (tenant_id, name, acl_groups, uploaded_by)
        values (
            v_recall_other,
            'other-' || v_index::text || '.txt',
            array['all'],
            v_member
        )
        returning id into v_document_id;
        insert into public.docqa_chunks (
            tenant_id, document_id, document_name, location, content, embedding
        )
        values (
            v_recall_other, v_document_id,
            'other-' || v_index::text || '.txt', 'section 1',
            'Unrelated cross-tenant vector context.', v_vector
        );
    end loop;

    select count(*), max(h.document_name) into v_hit_count, v_hit_name
    from public.docqa_search_chunks(
        v_recall_tenant,
        array['all']::text[],
        'What is retention period for invoices?',
        v_vector,
        10,
        30,
        60
    ) h
    where h.document_name = 'recall-target.txt';
    if v_hit_count <> 1 or v_hit_name <> 'recall-target.txt' then
        raise exception 'multi-tenant HNSW search did not recall the eligible tenant chunk';
    end if;

    select count(*), max(h.document_name) into v_hit_count, v_hit_name
    from public.docqa_search_chunks(
        v_workspace_tenant,
        array['staff']::text[],
        'What is the retention period for invoices?',
        v_vector,
        10,
        30,
        60
    ) h;
    if v_hit_count <> 1 or v_hit_name <> 'invoice-retention.txt' then
        raise exception 'tenant/ACL-filtered natural-language retrieval returned unexpected hits';
    end if;

    select count(*), max(h.document_name) into v_hit_count, v_hit_name
    from public.docqa_search_chunks(
        v_workspace_tenant,
        array['Finance']::text[],
        'What is the retention period for invoices?',
        v_vector,
        10,
        30,
        60
    ) h;
    if v_hit_count <> 1 or v_hit_name <> 'finance-invoice.txt' then
        raise exception 'ACL filtering allowed the wrong document group';
    end if;

    select count(*), max(h.document_name) into v_hit_count, v_hit_name
    from public.docqa_search_chunks(
        v_other_tenant,
        array['staff']::text[],
        'What is the retention period for invoices?',
        v_vector,
        10,
        30,
        60
    ) h;
    if v_hit_count <> 1 or v_hit_name <> 'other-tenant-invoice.txt' then
        raise exception 'tenant filtering allowed cross-tenant document access';
    end if;

    insert into public.docqa_audit_events (
        tenant_id, user_id, action, question, outcome
    )
    values (
        v_workspace_tenant, v_owner, 'query',
        'What is the retention period for invoices?', 'ok'
    );
    update auth.users u
       set raw_app_meta_data = jsonb_build_object(
           'tenant_id', v_workspace_tenant,
           'groups', array['owner']::text[],
           'roles', array['admin']::text[],
           'workspace_type', 'organization'
       )
     where u.id = v_owner;
    update auth.users u
       set raw_app_meta_data = jsonb_build_object(
           'tenant_id', v_workspace_tenant,
           'groups', array['staff']::text[],
           'roles', array[]::text[],
           'workspace_type', 'organization'
       )
     where u.id = v_member;

    v_expected_error := false;
    begin
        perform public.docqa_create_workspace_offboarding_request(
            v_workspace_tenant, v_member
        );
    exception when others then
        if sqlerrm = 'workspace administrator access is required' then
            v_expected_error := true;
        else
            raise;
        end if;
    end;
    if not v_expected_error then
        raise exception 'a non-admin unexpectedly requested workspace offboarding';
    end if;

    perform public.docqa_create_workspace_offboarding_request(
        v_workspace_tenant, v_owner
    );
    select public.docqa_review_workspace_offboarding_request(
        (
            select r.request_id
            from public.docqa_workspace_offboarding_requests r
            where r.tenant_id = v_workspace_tenant
              and r.status = 'pending'
        ),
        v_platform_admin,
        true,
        'SQL integration offboarding'
    ) into v_offboarding;

    if v_offboarding ->> 'status' <> 'approved'
       or not public.docqa_is_workspace_closed(v_workspace_tenant) then
        raise exception 'approved offboarding did not close the workspace';
    end if;
    if exists (
        select 1 from public.docqa_workspaces w
        where w.tenant_id = v_workspace_tenant
    ) or exists (
        select 1 from public.docqa_workspace_members m
        where m.tenant_id = v_workspace_tenant
    ) or exists (
        select 1 from public.docqa_documents d
        where d.tenant_id = v_workspace_tenant
    ) or exists (
        select 1
        from public.docqa_chunks c
        where c.tenant_id = v_workspace_tenant
    ) or exists (
        select 1 from public.docqa_audit_events e
        where e.tenant_id = v_workspace_tenant
    ) or exists (
        select 1 from public.docqa_organization_request_audit a
        where a.request_id = v_request_id
    ) or exists (
        select 1 from public.docqa_registered_organizations o
        where o.microsoft_tenant_id = v_microsoft_tenant
    ) then
        raise exception 'approved offboarding left active workspace or organization data behind';
    end if;
    if not exists (
        select 1 from auth.users u
        where u.id = v_owner
          and u.raw_app_meta_data ->> 'tenant_id' is null
    ) or not exists (
        select 1 from auth.users u
        where u.id = v_member
          and u.raw_app_meta_data ->> 'tenant_id' is null
    ) or (
        select count(*) from auth.users u
        where u.id in (v_owner, v_member)
    ) <> 2 then
        raise exception 'offboarding did not clear workspace claims or preserve the Auth account';
    end if;

    if exists (
        select 1
        from pg_class c
        join pg_namespace n on n.oid = c.relnamespace
        cross join (values ('anon'), ('authenticated')) roles(role)
        where n.nspname = 'public'
          and c.relname like 'docqa_%'
          and c.relkind in ('r', 'p', 'S')
          and (
              has_table_privilege(roles.role, c.oid, 'SELECT')
              or has_table_privilege(roles.role, c.oid, 'INSERT')
              or has_table_privilege(roles.role, c.oid, 'UPDATE')
              or has_table_privilege(roles.role, c.oid, 'DELETE')
              or has_table_privilege(roles.role, c.oid, 'TRUNCATE')
              or has_table_privilege(roles.role, c.oid, 'REFERENCES')
              or has_table_privilege(roles.role, c.oid, 'TRIGGER')
              or has_sequence_privilege(roles.role, c.oid, 'USAGE')
              or has_sequence_privilege(roles.role, c.oid, 'SELECT')
              or has_sequence_privilege(roles.role, c.oid, 'UPDATE')
          )
    ) then
        raise exception 'anon or authenticated retained table or sequence privileges';
    end if;

    if exists (
        select 1
        from pg_proc p
        join pg_namespace n on n.oid = p.pronamespace
        cross join (values ('anon'), ('authenticated')) roles(role)
        where n.nspname = 'public'
          and p.proname like 'docqa_%'
          and has_function_privilege(roles.role, p.oid, 'EXECUTE')
    ) then
        raise exception 'anon or authenticated retained execute privileges on a docqa function';
    end if;
end;
$docqa_integration$;

rollback;
