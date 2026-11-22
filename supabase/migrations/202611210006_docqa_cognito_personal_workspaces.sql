alter table public.docqa_workspaces
    drop constraint docqa_workspaces_provider_check;
alter table public.docqa_workspaces
    add constraint docqa_workspaces_provider_check
    check (provider in ('google', 'azure', 'cognito', 'legacy'));

do $migration$
declare
    v_function record;
    v_definition text;
    v_changed integer;
begin
    for v_function in
        select p.oid, p.proname, pg_get_functiondef(p.oid) as definition
        from pg_proc p
        join pg_namespace n on n.oid = p.pronamespace
        where n.nspname = 'public'
          and p.proname = any(array[
              'docqa_provision_trial_workspace',
              'docqa_register_current_member'
          ])
          and p.prosrc like '%p_provider%'
    loop
        v_definition := v_function.definition;
        v_changed := 0;

        if position('p_provider not in (''google'', ''azure'')' in v_definition) > 0 then
            v_definition := replace(
                v_definition,
                'p_provider not in (''google'', ''azure'')',
                'p_provider not in (''google'', ''azure'', ''cognito'')'
            );
            v_changed := v_changed + 1;
        end if;
        if position('p_provider not in (''google'', ''azure'', ''legacy'')' in v_definition) > 0 then
            v_definition := replace(
                v_definition,
                'p_provider not in (''google'', ''azure'', ''legacy'')',
                'p_provider not in (''google'', ''azure'', ''cognito'', ''legacy'')'
            );
            v_changed := v_changed + 1;
        end if;
        if position('p_provider = ''google'' and p_workspace_type <> ''personal''' in v_definition) > 0 then
            v_definition := replace(
                v_definition,
                'p_provider = ''google'' and p_workspace_type <> ''personal''',
                'p_provider in (''google'', ''cognito'') and p_workspace_type <> ''personal'''
            );
            v_definition := replace(
                v_definition,
                'Google preview accounts must use a personal workspace',
                'Personal preview accounts must use a personal workspace'
            );
            v_changed := v_changed + 1;
        end if;

        if v_changed = 0
           or position('p_provider not in (''google'', ''azure'')' in v_definition) > 0
           or position('p_provider = ''google'' and p_workspace_type <> ''personal''' in v_definition) > 0 then
            raise exception 'failed to add Cognito support to function %',
                v_function.oid::regprocedure;
        end if;
        execute v_definition;
    end loop;

    if (
        select count(*)
        from pg_proc p
        join pg_namespace n on n.oid = p.pronamespace
        where n.nspname = 'public'
          and p.proname = any(array[
              'docqa_provision_trial_workspace',
              'docqa_register_current_member'
          ])
    ) <> 2 then
        raise exception 'expected both workspace identity functions';
    end if;
end;
$migration$;
