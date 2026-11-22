create or replace function public.docqa_ensure_identity(
    p_user_id uuid,
    p_auth_provider text,
    p_issuer text,
    p_subject text,
    p_email text,
    p_email_verified boolean
)
returns uuid
language plpgsql
security definer
set search_path = pg_catalog, public, pg_temp
as $$
declare
    v_user_id uuid;
begin
    if p_user_id is null
       or p_auth_provider is null or length(btrim(p_auth_provider)) = 0
       or p_issuer is null or length(btrim(p_issuer)) = 0
       or p_subject is null or length(btrim(p_subject)) = 0
       or p_email_verified is null then
        raise exception 'invalid identity registration';
    end if;

    insert into public.docqa_identity_users (
        user_id, auth_provider, issuer, subject, email, email_verified
    )
    values (
        p_user_id, p_auth_provider, p_issuer, p_subject,
        nullif(btrim(p_email), ''), p_email_verified
    )
    on conflict (auth_provider, issuer, subject) do update
       set email = coalesce(excluded.email, public.docqa_identity_users.email),
           email_verified = case
               when excluded.email is null then public.docqa_identity_users.email_verified
               else excluded.email_verified
           end,
           updated_at = now()
     where public.docqa_identity_users.user_id = excluded.user_id
    returning user_id into v_user_id;

    if v_user_id is null then
        raise exception 'identity subject is already linked to another user';
    end if;
    return v_user_id;
end;
$$;

revoke all on function public.docqa_ensure_identity(uuid, text, text, text, text, boolean)
    from public, anon, authenticated;
grant execute on function public.docqa_ensure_identity(uuid, text, text, text, text, boolean)
    to service_role;
