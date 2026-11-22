"""Regression tests for the ingestion, index-loading, evaluation and CLI fixes."""
import pytest

from docqa.answer import ExtractiveBackend
from docqa.cli import make_embedder, parse_groups
from docqa.eval import matches_expected, run_eval
from docqa.grounding import figures
from docqa.index import HashEmbedder, HybridIndex
from docqa.ingest import chunk_text, ingest_folder, read_units


def write_txt(path, text="The notice period is 30 days."):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    return path


# ----------------------------------------------------------------- chunking
def test_long_text_without_punctuation_is_split():
    chunks = chunk_text("word " * 3000, max_chars=900, overlap=150)
    assert len(chunks) > 10 and all(len(c) <= 900 for c in chunks)
    assert len(chunk_text("x" * 2500, max_chars=900, overlap=100)) == 3   # hard cut, no spaces


def test_overlap_never_pushes_a_chunk_over_the_limit():
    text = " ".join(f"Clause {i} says something fairly long about item {i}." for i in range(80))
    for max_chars, overlap in [(120, 100), (200, 60), (300, 299), (900, 150)]:
        assert all(len(c) <= max_chars for c in chunk_text(text, max_chars, overlap))


def test_chunk_text_rejects_impossible_settings():
    with pytest.raises(ValueError, match="overlap"):
        chunk_text("abc", max_chars=100, overlap=100)
    with pytest.raises(ValueError, match="max_chars"):
        chunk_text("abc", max_chars=0, overlap=0)


# ------------------------------------------------------------------- folders
def test_subfolders_are_ingested_and_acl_is_keyed_by_relative_path(tmp_path):
    docs = tmp_path / "docs"
    write_txt(docs / "top.txt")
    write_txt(docs / "clients" / "acme" / "Letter.txt")
    write_txt(docs / "clients" / "zulu" / "Letter.txt")
    acl = {"clients/acme/Letter.txt": ["partners"], "clients/zulu/Letter.txt": ["finance"]}
    chunks = ingest_folder(docs, acl, default_acl=["admin"])
    by_doc = {c.doc: c.acl for c in chunks}
    assert by_doc == {"top.txt": ["admin"], "clients/acme/Letter.txt": ["partners"],
                      "clients/zulu/Letter.txt": ["finance"]}


def test_bare_file_name_acl_works_only_when_unambiguous(tmp_path):
    docs = tmp_path / "docs"
    write_txt(docs / "a" / "Unique.txt")
    write_txt(docs / "a" / "Same.txt")
    write_txt(docs / "b" / "Same.txt")
    issues = []
    chunks = ingest_folder(docs, {"Unique.txt": ["partners"], "Same.txt": ["partners"]},
                           default_acl=["admin"], issues=issues)
    by_doc = {c.doc: c.acl for c in chunks}
    assert by_doc["a/Unique.txt"] == ["partners"]
    assert by_doc["a/Same.txt"] == by_doc["b/Same.txt"] == ["admin"]    # not silently opened up
    assert sum("ambiguous" in i for i in issues) == 2


def test_acl_entries_that_match_nothing_are_reported(tmp_path):
    write_txt(tmp_path / "docs" / "real.txt")
    issues = []
    ingest_folder(tmp_path / "docs", {"renamed.txt": ["partners"]}, issues=issues)
    assert any("renamed.txt" in i and "matches no document" in i for i in issues)


def test_hidden_files_and_office_lock_files_are_skipped(tmp_path):
    docs = tmp_path / "docs"
    write_txt(docs / "real.txt")
    write_txt(docs / "~$lock.txt")
    write_txt(docs / ".hidden.txt")
    write_txt(docs / ".git" / "notes.txt")
    assert {c.doc for c in ingest_folder(docs)} == {"real.txt"}


def test_scanned_pdf_is_reported_and_not_indexed(tmp_path):
    from reportlab.pdfgen import canvas

    docs = tmp_path / "docs"
    docs.mkdir()
    c = canvas.Canvas(str(docs / "scan.pdf"))
    c.showPage()
    c.save()
    write_txt(docs / "ok.txt")
    issues = []
    chunks = ingest_folder(docs, issues=issues)
    assert {c.doc for c in chunks} == {"ok.txt"}
    assert any("scan.pdf" in i and "no extractable text" in i for i in issues)


def test_docx_keeps_paragraphs_and_tables_in_reading_order(tmp_path):
    from docx import Document

    doc = Document()
    doc.add_paragraph("Before table.")
    table = doc.add_table(rows=2, cols=2)
    table.rows[0].cells[0].text, table.rows[0].cells[1].text = "Fee", "R50,000"
    merged = table.rows[1].cells[0].merge(table.rows[1].cells[1])
    merged.text = "Merged row"
    doc.add_paragraph("After table.")
    path = tmp_path / "t.docx"
    doc.save(path)
    lines = read_units(path)[0][1].split("\n")
    assert lines == ["Before table.", "Fee | R50,000", "Merged row", "After table."]


def test_docx_can_be_parsed_in_isolated_worker(tmp_path):
    from docx import Document

    path = tmp_path / "isolated.docx"
    doc = Document()
    doc.add_paragraph("Isolated DOCX text.")
    doc.save(path)
    units = read_units(path, docx_parse_timeout=5)
    assert units == [("", "Isolated DOCX text.")]


def test_docx_parse_timeout_must_be_positive(tmp_path):
    from docx import Document

    path = tmp_path / "invalid.docx"
    doc = Document()
    doc.add_paragraph("Text")
    doc.save(path)
    with pytest.raises(ValueError, match="docx_parse_timeout"):
        read_units(path, docx_parse_timeout=0)


# --------------------------------------------------------------- index loading
def make_index(tmp_path, embedder):
    docs = tmp_path / "docs"
    write_txt(docs / "a.txt", "The annual audit fee is R50,000 excluding VAT.")
    write_txt(docs / "b.txt", "Passwords must be at least 12 characters long.")
    write_txt(docs / "c.txt", "Client working papers are retained for 7 years.")
    write_txt(docs / "d.txt", "Either party may terminate with 30 days written notice.")
    write_txt(docs / "e.txt", "Remote access requires multi-factor authentication.")
    idx = HybridIndex(embedder).build(ingest_folder(docs, default_acl=["all"]))
    idx.save(tmp_path / "idx")
    return tmp_path / "idx"


def test_loading_with_the_wrong_embedder_is_an_error(tmp_path):
    path = make_index(tmp_path, HashEmbedder(64))
    with pytest.raises(ValueError, match="hash-64"):
        HybridIndex.load(path, HashEmbedder(128))
    assert HybridIndex.load(path, HashEmbedder(64)).search("audit fee", user_groups=["all"])


def test_loading_embeddings_without_an_embedder_is_an_error_unless_bm25_only(tmp_path):
    path = make_index(tmp_path, HashEmbedder(64))
    with pytest.raises(ValueError, match="bm25_only"):
        HybridIndex.load(path)
    hits = HybridIndex.load(path, bm25_only=True).search("audit fee", user_groups=["all"])
    assert hits and hits[0].dense is None


def test_keyword_only_index_loads_without_an_embedder(tmp_path):
    path = make_index(tmp_path, None)
    assert HybridIndex.load(path).search("audit fee", user_groups=["all"])


def test_query_embedding_of_the_wrong_size_gives_a_clear_error(tmp_path):
    idx = HybridIndex(HashEmbedder(64)).build(
        ingest_folder(make_index(tmp_path, None).parent / "docs", default_acl=["all"])
    )
    idx.embedder = HashEmbedder(128)
    with pytest.raises(ValueError, match="different embedder"):
        idx.search("audit fee", user_groups=["all"])


def test_rrf_k_survives_a_save_and_load(tmp_path):
    docs = tmp_path / "docs"
    write_txt(docs / "a.txt")
    idx = HybridIndex(None, rrf_k=17).build(ingest_folder(docs))
    idx.save(tmp_path / "idx")
    assert HybridIndex.load(tmp_path / "idx").rrf_k == 17


# ----------------------------------------------------------------------- cli
def test_auto_embedder_follows_the_index(tmp_path):
    hashed = make_index(tmp_path / "h", HashEmbedder(64))
    keyword = make_index(tmp_path / "k", None)
    emb = make_embedder("auto", str(hashed))
    assert isinstance(emb, HashEmbedder) and emb.name == "hash-64"
    assert make_embedder("auto", str(keyword)) is None
    assert make_embedder("none") is None


def test_groups_are_stripped():
    assert parse_groups("partners, finance ,") == ["partners", "finance"]
    assert parse_groups("all") == ["all"]


# ---------------------------------------------------------------------- eval
def test_numbers_must_match_as_whole_numbers():
    assert matches_expected("7 years", "Papers are kept for 7 years [1].")
    assert not matches_expected("7 years", "Papers are kept for 17 years [1].")
    assert not matches_expected("R5,000", "The fee is R15,000.")
    assert not matches_expected("R5,000", "The fee is R5,000,000.")
    assert matches_expected("R5,000", "The fee is R 5 000.")
    assert matches_expected("R5,000", "The fee is R5,000.00 excluding VAT.")
    assert matches_expected("Aisha Patel", "The partner is Aisha Patel [1].")
    assert not matches_expected("R5,000", "The fee is R5,000.50.")


def test_a_citation_mark_or_next_sentence_does_not_break_a_number_match():
    # the number is followed by "[1]" or by another sentence that starts with digits
    assert matches_expected("R60,950", "Total due: R60,950. [1]")
    assert matches_expected("R60,950", "Total due: R60,950 [1][2]")
    assert matches_expected("R60,950", "Total due: R60,950.\n2025 is the invoice year.")
    assert matches_expected("30 days", "Either party may terminate with 30 days written notice [3].")


def test_unanswerable_passes_only_on_a_genuine_refusal():
    from docqa.synth import write_all
    import json, tempfile
    from pathlib import Path

    d = Path(tempfile.mkdtemp())
    write_all(d)
    idx = HybridIndex(HashEmbedder()).build(
        ingest_folder(d / "docs", acl=json.loads((d / "acl.json").read_text()))
    )
    qs = [q for q in json.loads((d / "questions.json").read_text()) if q["kind"] == "unanswerable"]

    class Garbage:
        def generate(self, q, hits, messages):
            return "blah blah"            # no citation: caught by a guard, not a real refusal

    bad = run_eval(idx, Garbage(), qs, verbose=False)["summary"]["by_kind"]["unanswerable"]
    assert bad["passed"] == 0 and bad["guard_rejections"] == bad["n"]
    good = run_eval(idx, ExtractiveBackend(), qs, verbose=False)["summary"]["by_kind"]["unanswerable"]
    assert good["passed"] == good["n"] and good["guard_rejections"] == 0


# ----------------------------------------------------------------- grounding
def test_figures_keep_a_year_and_a_count_apart_but_merge_real_thousands():
    assert figures("Invoice 2024 100 units") == {"2024", "100"}
    assert figures("R48,500") == figures("R48 500") == figures("R48\u00a0500") == {"48500"}
    assert figures("R 1 000 000 total") == {"1000000"}
