"""Command line: works on a laptop or a plain Linux VPS, no Docker required.

  python -m docqa.cli synth  --out data
  python -m docqa.cli index  --docs data/docs --acl data/acl.json --out data/index
  python -m docqa.cli ask    --index data/index "What is the fee for ...?" --groups partners
  python -m docqa.cli eval   --index data/index --questions data/questions.json
"""
from __future__ import annotations

import argparse
from pathlib import Path

from .answer import ExtractiveBackend, HFBackend, OllamaBackend, answer
from .eval import format_report, load_questions, run_eval, save_results
from .index import HashEmbedder, HybridIndex, SentenceTransformerEmbedder, index_embedder_name
from .ingest import ingest_folder, load_acl
from .profile import load_profile
from .synth import write_all


def parse_groups(value: str) -> list[str]:
    """'partners, finance' -> ['partners', 'finance'] (whitespace and empty items dropped)."""
    return [g.strip() for g in value.split(",") if g.strip()]


def make_embedder(name: str, index_dir: str | None = None):
    """Build an embedder from its CLI name. "auto" reuses the one the index was built with."""
    if name == "auto":
        name = index_embedder_name(index_dir) if index_dir else None
        if name is None:
            return None
    if name == "none":
        return None
    if name.startswith("hash"):
        _, _, dim = name.partition("-")
        return HashEmbedder(int(dim)) if dim.isdigit() else HashEmbedder()
    return SentenceTransformerEmbedder(name if name != "st" else "BAAI/bge-small-en-v1.5")


def make_backend(args):
    if args.backend == "extractive":
        return ExtractiveBackend()
    if args.backend == "ollama":
        return OllamaBackend(model=args.model or "qwen2.5:3b")
    return HFBackend(model_id=args.model or "Qwen/Qwen2.5-3B-Instruct", load_in_4bit=args.four_bit)


def main(argv: list[str] | None = None) -> None:
    p = argparse.ArgumentParser(prog="docqa")
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("synth", help="generate synthetic documents + questions")
    s.add_argument("--out", default="data")
    s.add_argument("--seed", type=int, default=7)

    i = sub.add_parser("index", help="ingest documents and build the search index")
    i.add_argument("--docs", required=True)
    i.add_argument("--acl", default=None)
    i.add_argument("--out", default="data/index")
    i.add_argument("--embedder", default="st", help="st | hash | none | <sentence-transformers model>")
    i.add_argument("--profile", default=None, help="customer profile JSON (chunk size, default access)")

    for name in ("ask", "eval"):
        a = sub.add_parser(name)
        a.add_argument("--index", required=True)
        a.add_argument("--embedder", default="auto",
                       help="auto (the one the index was built with) | st | hash | none | <model>")
        a.add_argument("--backend", default="extractive", choices=["extractive", "hf", "ollama"])
        a.add_argument("--model", default=None)
        a.add_argument("--four-bit", action="store_true")
        a.add_argument("--k", type=int, default=None, help="chunks per question (default: from profile)")
        a.add_argument("--profile", default=None, help="customer profile JSON (prompt, tone, rules)")
        if name == "ask":
            a.add_argument("question")
            a.add_argument("--groups", default=None,
                           help="comma-separated groups resolved from your identity provider")
            a.add_argument("--audit", default=None)
        else:
            a.add_argument("--questions", required=True)
            a.add_argument("--limit", type=int, default=None)
            a.add_argument("--out", default="results.json")

    args = p.parse_args(argv)

    if args.cmd == "synth":
        print(write_all(args.out, args.seed))
    elif args.cmd == "index":
        profile = load_profile(args.profile)
        issues: list[str] = []
        chunks = ingest_folder(args.docs, load_acl(args.acl), default_acl=profile.default_acl,
                               max_chars=profile.max_chars, overlap=profile.overlap, issues=issues)
        for message in issues:
            print(f"WARNING: {message}")
        idx = HybridIndex(make_embedder(args.embedder)).build(chunks)
        idx.save(args.out)
        print(f"Indexed {len(chunks)} chunks from {len({c.doc for c in chunks})} documents -> {args.out}")
    elif args.cmd == "ask":
        if not args.groups:
            raise SystemExit(
                "ERROR: --groups is required. Resolve the caller's groups from the "
                "identity provider before invoking docqa."
            )
        if not Path(args.index).is_dir():
            raise SystemExit(f"ERROR: index directory not found: {args.index}")
        idx = HybridIndex.load(args.index, make_embedder(args.embedder, args.index))
        ans = answer(args.question, idx, make_backend(args), k=args.k,
                     user_groups=parse_groups(args.groups), audit_path=args.audit,
                     profile=load_profile(args.profile))
        print(ans.text)
        for c in ans.citations:
            print(f"  [{c['n']}] {c['doc']} ({c['loc']})")
            if c.get("quote"):
                print(f"      \"{c['quote']}\"")
    elif args.cmd == "eval":
        idx = HybridIndex.load(args.index, make_embedder(args.embedder, args.index))
        result = run_eval(idx, make_backend(args), load_questions(args.questions),
                          k=args.k, limit=args.limit, profile=load_profile(args.profile))
        save_results(result, args.out)
        print("\n" + format_report(result))
        Path(args.out).with_suffix(".md").write_text(format_report(result))


if __name__ == "__main__":
    main()
