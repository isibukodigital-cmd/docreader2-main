"""Direct PostgreSQL transport for the application's document-store RPCs."""
from __future__ import annotations

import json
import math
import re
from typing import Any

from .supabase_store import (
    DocumentLimitError,
    DocumentStoreError,
    LastPlatformAdminError,
    OffboardingRequestConflictError,
    OrganizationRequestConflictError,
    PlatformAdminTargetNotFoundError,
    SupabaseVectorStore,
    TrialMemberLimitError,
    UnconfirmedOrganizationRequesterError,
)

_FUNCTION_NAME = re.compile(r"docqa_[a-z0-9_]+\Z")
_ARGUMENT_NAME = re.compile(r"p_[a-z0-9_]+\Z")
_JSONB_ARGUMENTS = {"p_app_metadata", "p_chunks", "p_selected_groups"}
_AUDIT_FIELDS = {
    "tenant_id",
    "user_id",
    "action",
    "question",
    "document_name",
    "documents_retrieved",
    "documents_cited",
    "outcome",
}


class PostgresVectorStore(SupabaseVectorStore):
    """Run the same parameterized store operations over a direct PostgreSQL pool."""

    def __init__(
        self,
        database_url: str,
        embedder,
        timeout: float = 20,
        pool=None,
    ):
        if not database_url:
            raise ValueError("DOCQA_DATABASE_URL is required for the postgres document store")
        self.embedder = embedder
        self.timeout = timeout
        self._db_errors: tuple[type[BaseException], ...] = ()
        if pool is not None:
            self.pool = pool
            return

        import psycopg
        from psycopg.rows import dict_row
        from psycopg_pool import ConnectionPool, PoolTimeout

        self._db_errors = (psycopg.Error, PoolTimeout)
        self.pool = ConnectionPool(
            conninfo=database_url,
            min_size=1,
            max_size=10,
            timeout=timeout,
            kwargs={
                "row_factory": dict_row,
                "connect_timeout": max(1, int(timeout)),
            },
            open=False,
        )
        try:
            self.pool.open(wait=True, timeout=timeout)
        except self._db_errors:
            self.pool.close()
            raise

    def close(self) -> None:
        self.pool.close()

    def _request(self, method: str, path: str, **kwargs: Any) -> Any:
        if method != "POST":
            raise DocumentStoreError("PostgreSQL store supports only POST operations")
        payload = kwargs.get("json")
        if not isinstance(payload, dict):
            raise DocumentStoreError("PostgreSQL store operation requires an object payload")

        if path == "docqa_audit_events":
            unknown_fields = set(payload) - _AUDIT_FIELDS
            if not payload or unknown_fields:
                raise DocumentStoreError("Invalid audit record")
            columns = list(payload)
            placeholders = ["%s"] * len(columns)
            statement = (
                "INSERT INTO public.docqa_audit_events "
                f"({', '.join(columns)}) VALUES ({', '.join(placeholders)})"
            )
            values = [payload[column] for column in columns]
            try:
                with self.pool.connection() as connection:
                    with connection.cursor() as cursor:
                        cursor.execute(statement, values)
                return None
            except self._db_errors as exc:
                raise DocumentStoreError("PostgreSQL audit write failed") from exc

        if not path.startswith("rpc/"):
            raise DocumentStoreError("Unsupported PostgreSQL store operation")
        function_name = path[4:]
        if not _FUNCTION_NAME.fullmatch(function_name):
            raise DocumentStoreError("Invalid PostgreSQL store function")
        arguments = list(payload.items())
        if any(not _ARGUMENT_NAME.fullmatch(name) for name, _ in arguments):
            raise DocumentStoreError("Invalid PostgreSQL store argument")

        try:
            with self.pool.connection() as connection:
                with connection.cursor() as cursor:
                    cursor.execute(
                        """
                        SELECT p.proretset, p.proargnames[1:p.pronargs] AS argument_names
                        FROM pg_proc AS p
                        JOIN pg_namespace AS n ON n.oid = p.pronamespace
                        WHERE n.nspname = 'public'
                          AND p.proname = %s
                          AND p.pronargs = %s
                        """,
                        (function_name, len(arguments)),
                    )
                    definitions = cursor.fetchall()
                    if len(definitions) != 1:
                        raise DocumentStoreError(
                            f"Expected one public PostgreSQL function for {function_name}"
                        )
                    definition = definitions[0]
                    expected_names = definition["argument_names"] or []
                    if set(expected_names) != {name for name, _ in arguments}:
                        raise DocumentStoreError(
                            f"PostgreSQL function arguments do not match for {function_name}"
                        )

                    expressions = []
                    values = []
                    for name, value in arguments:
                        if name == "p_query_embedding":
                            if (
                                not isinstance(value, (list, tuple))
                                or len(value) != 384
                                or any(
                                    not isinstance(item, (int, float))
                                    or not math.isfinite(float(item))
                                    for item in value
                                )
                            ):
                                raise DocumentStoreError("Invalid query embedding")
                            value = "[" + ",".join(format(float(item), ".9g") for item in value) + "]"
                            placeholder = "(%s)::extensions.vector(384)"
                        elif name in _JSONB_ARGUMENTS:
                            value = json.dumps(value, separators=(",", ":"), ensure_ascii=False)
                            placeholder = "%s::jsonb"
                        else:
                            placeholder = "%s"
                        expressions.append(f"{name} => {placeholder}")
                        values.append(value)

                    statement = (
                        f"SELECT * FROM public.{function_name}"
                        f"({', '.join(expressions)})"
                    )
                    cursor.execute(statement, values)
                    rows = cursor.fetchall()
                    if definition["proretset"]:
                        return rows
                    if not rows:
                        return None
                    row = rows[0]
                    if len(row) == 1:
                        return next(iter(row.values()))
                    return row
        except self._db_errors as exc:
            self._raise_operation_error(method, path, payload, exc)

    @staticmethod
    def _raise_operation_error(method: str, path: str, payload: dict, exc: BaseException) -> None:
        diagnostic = getattr(exc, "diag", None)
        message = getattr(diagnostic, "message_primary", None) or str(exc)
        lowered = message.lower()
        if path == "rpc/docqa_ingest_document" and "trial document limit reached" in lowered:
            raise DocumentLimitError("Trial document limit reached") from exc
        if (
            path in {"rpc/docqa_provision_trial_workspace", "rpc/docqa_register_current_member"}
            and "trial member limit reached" in lowered
        ):
            member_limit = payload.get("p_member_limit")
            raise TrialMemberLimitError(
                "Trial workspace member limit reached",
                member_limit if isinstance(member_limit, int) else None,
            ) from exc
        if (
            path == "rpc/docqa_grant_platform_admin"
            and "confirmed platform administrator target not found" in lowered
        ):
            raise PlatformAdminTargetNotFoundError(
                "No confirmed identity-provider user was found for that email"
            ) from exc
        if (
            path == "rpc/docqa_revoke_platform_admin"
            and "cannot remove the last platform administrator" in lowered
        ):
            raise LastPlatformAdminError(
                "At least one platform administrator must remain"
            ) from exc
        if path in {
            "rpc/docqa_create_organization_request",
            "rpc/docqa_review_organization_request",
        } and any(
            text in lowered
            for text in ("organization already registered", "organization request is not pending")
        ):
            raise OrganizationRequestConflictError(
                "The organization request has changed. Refresh and try again."
            ) from exc
        if path in {
            "rpc/docqa_create_workspace_offboarding_request",
            "rpc/docqa_review_workspace_offboarding_request",
        } and any(
            text in lowered
            for text in (
                "offboarding request is already pending",
                "offboarding request is no longer pending",
                "workspace has already been closed",
                "workspace has been closed",
                "workspace to offboard was not found",
            )
        ):
            raise OffboardingRequestConflictError(
                "The workspace offboarding request has changed. Refresh and try again."
            ) from exc
        if (
            path == "rpc/docqa_create_organization_request"
            and "confirmed organization requester not found" in lowered
        ):
            raise UnconfirmedOrganizationRequesterError(
                "Confirm the email address on your identity-provider account before requesting organization access."
            ) from exc
        raise DocumentStoreError(f"PostgreSQL request failed ({method} {path})") from exc
