#!/usr/bin/env python3
"""
Load the knowledge base into Supabase: read kb/*.md, cut into chunks,
turn each chunk into an embedding, and store it in kb_chunks.

Usage (PowerShell):
    python ingest_kb.py --dry-run                 # show chunks, no API calls, no database
    $env:DATABASE_URL = "postgresql://..."
    $env:EMBED_PROVIDER = "mistral"               # or "openai" or "gemini"
    $env:MISTRAL_API_KEY = "..."                  # or OPENAI_API_KEY / GEMINI_API_KEY
    python ingest_kb.py

Vector sizes: openai and gemini use 768 (run 003_kb.sql); mistral uses 1024
(run 003_kb_1024.sql instead). The table size must match the provider.

Running it again is safe: it deletes and re-inserts the chunks of each document.

IMPORTANT: the model used here MUST be the same one your n8n search_kb tool uses
to embed customer questions. Different models = different "languages" of numbers,
and search results become garbage.
"""
import argparse
import json
import math
import os
import re
import sys
import time
from pathlib import Path

import requests

PROVIDER_DIMS = {"openai": 768, "gemini": 768, "mistral": 1024}   # numbers per vector
MAX_CHARS = 1100                      # a section longer than this is split by paragraph
KB_DIR = Path(__file__).resolve().parent.parent / "kb"


# ------------------------------------------------------------------
# PART 1: CHUNKING (pure Python, no network)
# ------------------------------------------------------------------
def split_long(text, limit=MAX_CHARS):
    """Split a long section on blank lines so every piece stays under `limit`."""
    parts, current = [], ""
    for para in re.split(r"\n\s*\n", text.strip()):
        if current and len(current) + len(para) + 2 > limit:
            parts.append(current.strip())
            current = para
        else:
            current = f"{current}\n\n{para}" if current else para
    if current.strip():
        parts.append(current.strip())
    return parts


def chunk_document(path):
    """One chunk per '## ' section. Each chunk starts with 'Title - Section' so the
    AI (and the embedding) always know where the text came from."""
    raw = path.read_text(encoding="utf-8")
    title_match = re.search(r"^# (.+)$", raw, flags=re.MULTILINE)
    title = title_match.group(1).strip() if title_match else path.stem
    doc_id = path.stem

    chunks, index = [], 0
    for block in re.split(r"^## ", raw, flags=re.MULTILINE)[1:]:
        heading, _, body = block.partition("\n")
        heading, body = heading.strip(), body.strip()
        if not body:
            continue
        for piece in split_long(body):
            chunks.append({
                "doc_id": doc_id,
                "title": title,
                "section": heading,
                "chunk_index": index,
                "content": f"{title} - {heading}\n\n{piece}",
                "metadata": {"doc_id": doc_id, "title": title, "section": heading,
                             "source": f"kb/{path.name}"},
            })
            index += 1
    return chunks


def load_all_chunks():
    files = sorted(KB_DIR.glob("*.md"))
    if not files:
        sys.exit(f"No .md files found in {KB_DIR}")
    return [c for f in files for c in chunk_document(f)]


# ------------------------------------------------------------------
# PART 2: EMBEDDINGS (calls an outside API)
# ------------------------------------------------------------------
def normalize(vec):
    """Scale to length 1. Cosine search works best with unit vectors."""
    norm = math.sqrt(sum(x * x for x in vec)) or 1.0
    return [x / norm for x in vec]


def embed_openai(texts, dims):
    key = os.environ.get("OPENAI_API_KEY") or sys.exit("Set OPENAI_API_KEY")
    r = requests.post(
        "https://api.openai.com/v1/embeddings",
        headers={"Authorization": f"Bearer {key}"},
        json={"model": "text-embedding-3-small", "input": texts, "dimensions": dims},
        timeout=60,
    )
    r.raise_for_status()
    data = sorted(r.json()["data"], key=lambda d: d["index"])
    return [normalize(d["embedding"]) for d in data]


def embed_mistral(texts, dims):
    """mistral-embed always returns 1024 numbers (it has no size option)."""
    key = os.environ.get("MISTRAL_API_KEY") or sys.exit("Set MISTRAL_API_KEY")
    for attempt in range(6):                      # free tier is rate limited: wait and retry
        r = requests.post(
            "https://api.mistral.ai/v1/embeddings",
            headers={"Authorization": f"Bearer {key}"},
            json={"model": "mistral-embed", "input": texts},
            timeout=60,
        )
        if r.status_code == 429:
            wait = 2 ** attempt
            print(f"  rate limited, waiting {wait}s...")
            time.sleep(wait)
            continue
        r.raise_for_status()
        data = sorted(r.json()["data"], key=lambda d: d["index"])
        time.sleep(1.1)                           # about 1 request per second
        return [normalize(d["embedding"]) for d in data]
    sys.exit("Mistral kept returning 429 (rate limit). Wait a minute and run again.")


def embed_gemini(texts, dims):
    key = os.environ.get("GEMINI_API_KEY") or sys.exit("Set GEMINI_API_KEY")
    out = []
    for text in texts:
        r = requests.post(
            "https://generativelanguage.googleapis.com/v1beta/models/gemini-embedding-001:embedContent",
            headers={"x-goog-api-key": key},
            json={"content": {"parts": [{"text": text}]},
                  "taskType": "RETRIEVAL_DOCUMENT",
                  "outputDimensionality": dims},
            timeout=60,
        )
        r.raise_for_status()
        out.append(normalize(r.json()["embedding"]["values"]))
        time.sleep(0.3)               # stay under free-tier rate limits
    return out


def embed(texts, provider):
    dims = PROVIDER_DIMS[provider]
    fn = {"openai": embed_openai, "gemini": embed_gemini, "mistral": embed_mistral}[provider]
    vectors = []
    for i in range(0, len(texts), 32):   # batches of 32
        vectors += fn(texts[i:i + 32], dims)
    bad = [len(v) for v in vectors if len(v) != dims]
    if bad:
        sys.exit(f"Model returned {bad[0]} numbers per vector, expected {dims}.")
    return vectors


# ------------------------------------------------------------------
# PART 3: LOAD INTO POSTGRES
# ------------------------------------------------------------------
def load(database_url, chunks, vectors):
    import psycopg2
    from psycopg2.extras import execute_values

    conn = psycopg2.connect(database_url)
    with conn, conn.cursor() as cur:             # one transaction: all or nothing
        cur.execute("delete from kb_chunks where doc_id = any(%s)",
                    (sorted({c["doc_id"] for c in chunks}),))
        execute_values(
            cur,
            """insert into kb_chunks (doc_id, title, section, chunk_index, content, metadata, embedding)
               values %s""",
            [(c["doc_id"], c["title"], c["section"], c["chunk_index"], c["content"],
              json.dumps(c["metadata"]), "[" + ",".join(f"{x:.6f}" for x in v) + "]")
             for c, v in zip(chunks, vectors)],
            template="(%s,%s,%s,%s,%s,%s::jsonb,%s::vector)",
        )
    conn.close()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true",
                        help="print the chunks only: no API calls, no database")
    args = parser.parse_args()

    chunks = load_all_chunks()
    docs = len({c["doc_id"] for c in chunks})
    sizes = [len(c["content"]) for c in chunks]
    print(f"{docs} documents -> {len(chunks)} chunks "
          f"(chars: min {min(sizes)}, avg {sum(sizes) // len(sizes)}, max {max(sizes)})")

    if args.dry_run:
        for c in chunks[:3]:
            print(f"\n--- {c['doc_id']} #{c['chunk_index']} [{c['section']}] ---\n{c['content']}")
        print("\nDry run: nothing embedded, nothing saved.")
        return

    provider = os.environ.get("EMBED_PROVIDER", "openai").lower()
    if provider not in PROVIDER_DIMS:
        sys.exit("EMBED_PROVIDER must be 'openai', 'gemini' or 'mistral'")
    database_url = os.environ.get("DATABASE_URL") or sys.exit("Set DATABASE_URL")

    print(f"Embedding with {provider} ({PROVIDER_DIMS[provider]} numbers per chunk)...")
    vectors = embed([c["content"] for c in chunks], provider)
    load(database_url, chunks, vectors)
    print(f"Done: {len(chunks)} chunks stored.")


if __name__ == "__main__":
    main()
