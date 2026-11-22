from contextlib import contextmanager

from docqa.postgres_store import PostgresVectorStore


class FakeCursor:
    def __init__(
        self,
        argument_names=("p_user_id", "p_app_metadata"),
        proretset=False,
        rpc_rows=None,
    ):
        self.executions = []
        self.rows = []
        self.argument_names = argument_names
        self.proretset = proretset
        self.rpc_rows = rpc_rows or [{"docqa_update_identity_app_metadata": None}]

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def execute(self, statement, parameters):
        self.executions.append((statement, parameters))
        if "FROM pg_proc" in statement:
            self.rows = [{
                "proretset": self.proretset,
                "argument_names": list(self.argument_names),
            }]
        else:
            self.rows = self.rpc_rows

    def fetchall(self):
        return self.rows


class FakeConnection:
    def __init__(self, cursor):
        self._cursor = cursor

    def cursor(self):
        return self._cursor


class FakePool:
    def __init__(self, **cursor_options):
        self.cursor = FakeCursor(**cursor_options)
        self.closed = False

    @contextmanager
    def connection(self):
        yield FakeConnection(self.cursor)

    def close(self):
        self.closed = True


def test_postgres_store_calls_rpc_with_named_jsonb_parameters():
    pool = FakePool()
    store = PostgresVectorStore("postgresql://db", embedder=None, pool=pool)

    store.update_identity_app_metadata(
        "user-1",
        {"tenant_id": "tenant-a", "groups": ["staff"]},
    )

    statement, parameters = pool.cursor.executions[1]
    assert "p_user_id => %s" in statement
    assert "p_app_metadata => %s::jsonb" in statement
    assert parameters == [
        "user-1",
        '{"tenant_id":"tenant-a","groups":["staff"]}',
    ]

    store.close()
    assert pool.closed


def test_postgres_store_requires_database_url():
    try:
        PostgresVectorStore("", embedder=None, pool=FakePool())
    except ValueError as exc:
        assert "DOCQA_DATABASE_URL" in str(exc)
    else:
        raise AssertionError("Expected missing database URL to be rejected")


def test_postgres_search_casts_embedding_and_returns_rpc_rows():
    rows = [{
        "chunk_id": "chunk-1",
        "document_name": "handbook.pdf",
        "location": "page 1",
        "content": "Relevant policy",
        "acl_groups": ["staff"],
        "score": 0.9,
        "lexical_score": 0.8,
        "semantic_score": 0.7,
    }]
    pool = FakePool(
        argument_names=(
            "p_tenant_id",
            "p_user_groups",
            "p_query",
            "p_query_embedding",
            "p_match_count",
            "p_candidate_count",
            "p_rrf_k",
        ),
        proretset=True,
        rpc_rows=rows,
    )

    class Embedder:
        def encode_query(self, _query):
            return [0.0] * 384

    store = PostgresVectorStore("postgresql://db", Embedder(), pool=pool)
    hits = store.search("policy", tenant_id="tenant-a", user_groups=["staff"])

    statement, parameters = pool.cursor.executions[1]
    assert "p_query_embedding => (%s)::extensions.vector(384)" in statement
    assert len(parameters[3].strip("[]").split(",")) == 384
    assert hits[0].chunk.text == "Relevant policy"


def test_postgres_store_waits_for_a_bounded_pool_on_startup(monkeypatch):
    import psycopg_pool

    class RecordingPool:
        def __init__(self, **options):
            self.options = options
            self.open_options = None

        def open(self, **options):
            self.open_options = options

        def close(self):
            pass

    monkeypatch.setattr(psycopg_pool, "ConnectionPool", RecordingPool)
    store = PostgresVectorStore("postgresql://db", embedder=None, timeout=7)

    assert store.pool.options["max_size"] == 10
    assert store.pool.options["kwargs"]["connect_timeout"] == 7
    assert store.pool.open_options == {"wait": True, "timeout": 7}
