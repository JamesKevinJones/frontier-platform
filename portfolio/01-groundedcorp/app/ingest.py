from __future__ import annotations

import re
import sqlite3
from pathlib import Path

from app.config import (
    CHUNK_OVERLAP,
    CHUNK_SIZE,
    CORPUS_DIR,
    SQLITE_PATH,
)
from app.embeddings import embed_text
from app.retrieval import reset_bm25
from app.vectorstore import VectorStore


def _connect() -> sqlite3.Connection:
    SQLITE_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(SQLITE_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS documents (
            doc_id TEXT PRIMARY KEY,
            title TEXT,
            dept TEXT,
            doc_type TEXT,
            version TEXT,
            path TEXT,
            body TEXT
        )
        """
    )
    return conn


def chunk_text(text: str, size: int = CHUNK_SIZE, overlap: int = CHUNK_OVERLAP) -> list[str]:
    words = text.split()
    if not words:
        return []
    chunks = []
    i = 0
    step = max(size - overlap, 1)
    while i < len(words):
        chunks.append(" ".join(words[i : i + size]))
        i += step
    return chunks


def _parse_frontmatter(raw: str) -> tuple[dict, str]:
    if raw.startswith("---"):
        parts = raw.split("---", 2)
        if len(parts) >= 3:
            meta = {}
            for line in parts[1].strip().splitlines():
                if ":" in line:
                    k, v = line.split(":", 1)
                    meta[k.strip()] = v.strip()
            return meta, parts[2].strip()
    return {}, raw.strip()


def ingest_corpus(corpus_dir: Path = CORPUS_DIR) -> dict:
    corpus_dir.mkdir(parents=True, exist_ok=True)
    store = VectorStore()
    store.clear()

    conn = _connect()
    conn.execute("DELETE FROM documents")

    ids, documents, embeddings, metadatas = [], [], [], []
    n_docs = 0
    for path in sorted(corpus_dir.glob("**/*")):
        if path.suffix.lower() not in {".md", ".txt"}:
            continue
        raw = path.read_text(encoding="utf-8")
        meta, body = _parse_frontmatter(raw)
        doc_id = meta.get("doc_id") or path.stem
        title = meta.get("title") or path.stem.replace("-", " ").title()
        dept = meta.get("dept") or "general"
        doc_type = meta.get("doc_type") or "policy"
        version = meta.get("version") or "1.0"

        conn.execute(
            "INSERT OR REPLACE INTO documents VALUES (?,?,?,?,?,?,?)",
            (doc_id, title, dept, doc_type, version, str(path), body),
        )
        n_docs += 1
        for i, chunk in enumerate(chunk_text(body)):
            cid = f"{doc_id}::c{i}"
            ids.append(cid)
            documents.append(chunk)
            embeddings.append(embed_text(chunk))
            metadatas.append(
                {
                    "doc_id": doc_id,
                    "title": title,
                    "dept": dept,
                    "doc_type": doc_type,
                    "version": version,
                    "chunk_index": i,
                }
            )

    if ids:
        store.add(ids=ids, documents=documents, embeddings=embeddings, metadatas=metadatas)
    conn.commit()
    conn.close()
    # Corpus statistics changed, so the cached IDF table is stale.
    reset_bm25()
    return {"documents": n_docs, "chunks": len(ids)}


def list_documents() -> list[dict]:
    conn = _connect()
    rows = conn.execute(
        "SELECT doc_id, title, dept, doc_type, version, path FROM documents ORDER BY dept, title"
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def bm25ish_score(query: str, text: str) -> float:
    q = set(re.findall(r"[a-z0-9]+", query.lower()))
    t = re.findall(r"[a-z0-9]+", text.lower())
    if not q or not t:
        return 0.0
    tf = sum(1 for w in t if w in q)
    return tf / (len(t) ** 0.5)
