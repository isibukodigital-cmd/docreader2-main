"""Install or upgrade DocReader's PostgreSQL schema on supported providers."""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

_GENERIC_POSTGRES_BOOTSTRAP = """
DO $bootstrap$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'anon') THEN
        CREATE ROLE anon NOLOGIN;
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'authenticated') THEN
        CREATE ROLE authenticated NOLOGIN;
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'service_role') THEN
        CREATE ROLE service_role NOLOGIN;
    END IF;
END
$bootstrap$;

CREATE SCHEMA IF NOT EXISTS auth;
CREATE SCHEMA IF NOT EXISTS extensions;
CREATE TABLE IF NOT EXISTS auth.users (
    id uuid PRIMARY KEY,
    email text UNIQUE,
    email_confirmed_at timestamptz,
    raw_app_meta_data jsonb NOT NULL DEFAULT '{}'::jsonb,
    raw_user_meta_data jsonb NOT NULL DEFAULT '{}'::jsonb,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now()
);
"""

_GENERIC_POSTGRES_FINALIZE = """
DROP TRIGGER IF EXISTS docqa_sync_identity_metadata
    ON public.docqa_identity_users;
DROP FUNCTION IF EXISTS public.docqa_sync_identity_metadata_to_supabase_auth();
DROP TRIGGER IF EXISTS docqa_sync_identity_user ON auth.users;
DROP FUNCTION IF EXISTS public.docqa_sync_supabase_identity_user();

DO $finalize$
BEGIN
    IF EXISTS (
        SELECT 1
        FROM pg_proc AS p
        JOIN pg_namespace AS n ON n.oid = p.pronamespace
        WHERE n.nspname = 'public'
          AND p.prosrc LIKE '%auth.users%'
    ) THEN
        RAISE EXCEPTION 'active application functions still depend on auth.users';
    END IF;

    IF EXISTS (
        SELECT 1
        FROM pg_constraint AS c
        JOIN pg_class AS t ON t.oid = c.conrelid
        JOIN pg_namespace AS n ON n.oid = t.relnamespace
        WHERE c.contype = 'f'
          AND c.confrelid = 'auth.users'::regclass
          AND n.nspname = 'public'
    ) THEN
        RAISE EXCEPTION 'application foreign keys still depend on auth.users';
    END IF;
END
$finalize$;

DROP SCHEMA auth CASCADE;
"""

_MIGRATION_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS public.docqa_schema_migrations (
    version text PRIMARY KEY,
    applied_at timestamptz NOT NULL DEFAULT now()
)
"""


class SetupError(RuntimeError):
    """A safe, actionable database setup failure."""


def _migration_files(migration_directory: Path) -> list[Path]:
    migrations = sorted(migration_directory.glob("*.sql"), key=lambda path: path.name)
    if not migrations:
        raise SetupError(f"No SQL migrations found in {migration_directory}")
    return migrations


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Install or upgrade the DocReader PostgreSQL schema."
    )
    parser.add_argument(
        "--provider",
        choices=("postgres", "supabase"),
        required=True,
        help="Use postgres for Neon, RDS, or other standard PostgreSQL providers.",
    )
    parser.add_argument(
        "--database-url",
        help="Database-owner connection URI. Defaults to DOCQA_DATABASE_URL "
        "or SUPABASE_DB_URL according to --provider.",
    )
    parser.add_argument(
        "--yes",
        action="store_true",
        help="Skip the interactive target-database confirmation.",
    )
    return parser.parse_args(argv)


def _database_url(args: argparse.Namespace) -> str:
    if args.database_url:
        return args.database_url
    if args.provider == "supabase":
        return os.getenv("SUPABASE_DB_URL", "")
    return os.getenv("DOCQA_DATABASE_URL", "")


def _apply_script(connection, script: str) -> None:
    with connection.cursor() as cursor:
        cursor.execute(script, prepare=False)


def _database_state(connection) -> tuple[str, bool, bool, bool]:
    with connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT current_database(),
                   to_regclass('public.docqa_schema_migrations') IS NOT NULL,
                   to_regclass('public.docqa_documents') IS NOT NULL,
                   to_regclass('auth.users') IS NOT NULL
            """
        )
        return cursor.fetchone()


def _confirm(database_name: str, provider: str, assume_yes: bool) -> bool:
    if assume_yes:
        return True
    print(
        f"Provider: {provider}\n"
        f"Database: {database_name}\n"
        "This applies DocReader schema migrations to that database."
    )
    try:
        return input("Type 'setup' to continue: ").strip() == "setup"
    except EOFError:
        return False


def setup_database(
    connection,
    provider: str,
    migration_directory: Path,
    assume_yes: bool = False,
) -> None:
    if provider not in {"postgres", "supabase"}:
        raise SetupError("Provider must be 'postgres' or 'supabase'")

    database_name, has_history, has_docqa_schema, has_auth_users = _database_state(connection)
    connection.commit()
    if has_docqa_schema and not has_history:
        raise SetupError(
            "DocReader tables already exist without setup history. "
            "Use the existing provider's migration workflow instead of reinstalling."
        )
    if provider == "postgres" and has_auth_users and not has_history:
        raise SetupError(
            "The database already contains auth.users. Use --provider supabase "
            "for Supabase, or choose a fresh PostgreSQL database."
        )
    if not _confirm(database_name, provider, assume_yes):
        raise SetupError("Database setup was not confirmed")

    migrations = _migration_files(migration_directory)
    with connection.transaction():
        if provider == "postgres":
            _apply_script(connection, _GENERIC_POSTGRES_BOOTSTRAP)
        _apply_script(connection, _MIGRATION_TABLE_SQL)

    for migration in migrations:
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT EXISTS (SELECT 1 FROM public.docqa_schema_migrations "
                "WHERE version = %s)",
                (migration.name,),
            )
            already_applied = cursor.fetchone()[0]
        connection.commit()
        if already_applied:
            print(f"Already applied: {migration.name}")
            continue

        print(f"Applying: {migration.name}")
        sql = migration.read_text(encoding="utf-8")
        with connection.transaction():
            with connection.cursor() as cursor:
                cursor.execute(sql, prepare=False)
                cursor.execute(
                    "INSERT INTO public.docqa_schema_migrations (version) VALUES (%s)",
                    (migration.name,),
                )

    if provider == "postgres":
        with connection.transaction():
            _apply_script(connection, _GENERIC_POSTGRES_FINALIZE)

    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT to_regclass('public.docqa_documents') IS NOT NULL, "
            "to_regnamespace('auth') IS NULL"
        )
        has_documents, auth_schema_removed = cursor.fetchone()
    if not has_documents:
        raise SetupError("Schema setup completed without the document tables")
    if provider == "postgres" and not auth_schema_removed:
        raise SetupError("Temporary auth compatibility schema was not removed")


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    database_url = _database_url(args)
    if not database_url:
        variable = "SUPABASE_DB_URL" if args.provider == "supabase" else "DOCQA_DATABASE_URL"
        print(f"Provide --database-url or set {variable}.", file=sys.stderr)
        return 2
    if not database_url.startswith(("postgres://", "postgresql://")):
        print("Database URL must use postgres:// or postgresql://.", file=sys.stderr)
        return 2

    try:
        import psycopg
    except ModuleNotFoundError:
        print(
            'Install the setup dependency with: python -m pip install "psycopg[binary]>=3.2"',
            file=sys.stderr,
        )
        return 2

    try:
        root = Path(__file__).resolve().parents[1]
        migration_directory = root / "supabase" / "migrations"
        with psycopg.connect(database_url) as connection:
            setup_database(
                connection,
                args.provider,
                migration_directory,
                assume_yes=args.yes,
            )
    except SetupError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    except psycopg.Error as exc:
        print(f"PostgreSQL setup failed: {exc}", file=sys.stderr)
        return 1

    print("DocReader database setup completed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
