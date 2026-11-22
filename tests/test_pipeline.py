from io import BytesIO

import pytest
from pypdf import PdfWriter

from docqa.answer import NOT_FOUND_TEXT, ExtractiveBackend, answer
from docqa.eval import norm, run_eval
from docqa.index import HashEmbedder, HybridIndex
from docqa.ingest import (
    PDFPageLimitError,
    PDFParseTimeoutError,
    chunk_text,
    ingest_folder,
    load_acl,
    read_units,
)
from docqa.synth import write_all


def build(tmp_path):
    write_all(tmp_path)
    chunks = ingest_folder(tmp_path / "docs", load_acl(tmp_path / "acl.json"))
    return HybridIndex(HashEmbedder()).build(chunks), tmp_path


def test_chunking_respects_size_and_overlap():
    text = " ".join(f"Sentence number {i} is here." for i in range(60))
    chunks = chunk_text(text, max_chars=200, overlap=60)
    assert len(chunks) > 3
    assert all(len(c) <= 260 for c in chunks)


def test_chunks_carry_location_and_acl(tmp_path):
    index, _ = build(tmp_path)
    pdf = [c for c in index.chunks if c.doc.endswith(".pdf")]
    assert pdf and all(c.loc.startswith("p.") for c in pdf)
    eng = [c for c in index.chunks if c.doc.startswith("Engagement_Letter")]
    assert eng and all(c.acl == ["partners"] for c in eng)


def test_pdf_page_limit_rejects_before_extracting_pages(tmp_path):
    writer = PdfWriter()
    writer.add_blank_page(width=72, height=72)
    writer.add_blank_page(width=72, height=72)
    path = tmp_path / "over-limit.pdf"
    with path.open("wb") as file:
        writer.write(file)

    with pytest.raises(PDFPageLimitError, match="limit is 1"):
        read_units(path, max_pdf_pages=1, pdf_parse_timeout=30)


def test_pdf_parse_timeout_terminates_isolated_worker(tmp_path):
    writer = PdfWriter()
    writer.add_blank_page(width=72, height=72)
    path = tmp_path / "timeout.pdf"
    with path.open("wb") as file:
        writer.write(file)

    with pytest.raises(PDFParseTimeoutError):
        read_units(path, max_pdf_pages=500, pdf_parse_timeout=0.001)


def test_restricted_docs_never_returned_to_unprivileged_user(tmp_path):
    index, _ = build(tmp_path)
    for query in ["annual audit fee Karoo Fresh Produce", "total due invoice", "engagement partner"]:
        for hit in index.search(query, k=10, user_groups=["all"]):
            assert "partners" not in hit.chunk.acl or "all" in hit.chunk.acl


def test_missing_groups_cannot_reveal_private_documents(tmp_path):
    index, _ = build(tmp_path)
    for hit in index.search("annual audit fee", k=10, user_groups=None):
        assert "partners" not in hit.chunk.acl


def test_partner_can_retrieve_fee_chunk(tmp_path):
    index, _ = build(tmp_path)
    hits = index.search("What is the annual audit fee for Karoo Fresh Produce (Pty) Ltd?",
                        k=4, user_groups=["partners"])
    assert any(h.chunk.doc == "Engagement_Letter_Karoo_Fresh_Produce.docx" for h in hits)


def test_no_visible_chunks_means_refusal(tmp_path):
    index, _ = build(tmp_path)
    index.chunks = [c for c in index.chunks if "partners" in c.acl and "all" not in c.acl]
    index.build(index.chunks)
    ans = answer("annual audit fee", index, ExtractiveBackend(), user_groups=["all"])
    assert not ans.found and ans.text == NOT_FOUND_TEXT


def test_answer_without_citation_is_rejected(tmp_path):
    index, _ = build(tmp_path)

    class NoCite:
        def generate(self, q, hits, messages):
            return "The fee is R1."

    ans = answer("annual audit fee Karoo", index, NoCite(), user_groups=["partners"])
    assert not ans.found and ans.reason == "no_citation"


def test_not_found_marker_is_respected(tmp_path):
    index, _ = build(tmp_path)

    class Refuse:
        def generate(self, q, hits, messages):
            return "NOT_FOUND"

    ans = answer("anything", index, Refuse(), user_groups=["partners"])
    assert not ans.found and ans.reason == "not_found"


def test_audit_log_written(tmp_path):
    index, _ = build(tmp_path)
    log = tmp_path / "audit.jsonl"
    answer("annual audit fee Karoo", index, ExtractiveBackend(), user_groups=["partners"],
           user="tester", audit_path=log)
    assert '"user": "tester"' in log.read_text()


def test_eval_runs_and_forbidden_never_leaks(tmp_path):
    import json
    index, p = build(tmp_path)
    qs = json.loads((p / "questions.json").read_text())
    result = run_eval(index, ExtractiveBackend(), qs, verbose=False)
    assert result["summary"]["by_kind"]["forbidden"]["leaks"] == 0
    assert result["summary"]["by_kind"]["answerable"]["retrieval_hit_rate"] >= 0.7


def test_norm_makes_money_formats_comparable():
    assert norm("R48,500") == norm("R 48 500")
