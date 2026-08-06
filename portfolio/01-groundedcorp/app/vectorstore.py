"""Tiny persistent vector store (JSON) — avoids chromadb/numpy native builds."""

from __future__ import annotations

import json
from pathlib import Path

from app.config import CHROMA_DIR
from app.embeddings import cosine

STORE_PATH = CHROMA_DIR / "kb_store.json"


class VectorStore:
    def __init__(self, path: Path = STORE_PATH) -> None:
        self.path = path
        self.ids: list[str] = []
        self.documents: list[str] = []
        self.embeddings: list[list[float]] = []
        self.metadatas: list[dict] = []

    def clear(self) -> None:
        self.ids, self.documents, self.embeddings, self.metadatas = [], [], [], []
        if self.path.exists():
            self.path.unlink()

    def add(
        self,
        ids: list[str],
        documents: list[str],
        embeddings: list[list[float]],
        metadatas: list[dict],
    ) -> None:
        self.ids.extend(ids)
        self.documents.extend(documents)
        self.embeddings.extend(embeddings)
        self.metadatas.extend(metadatas)
        self.save()

    def count(self) -> int:
        return len(self.ids)

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "ids": self.ids,
            "documents": self.documents,
            "embeddings": self.embeddings,
            "metadatas": self.metadatas,
        }
        self.path.write_text(json.dumps(payload), encoding="utf-8")

    def load(self) -> "VectorStore":
        if not self.path.exists():
            return self
        payload = json.loads(self.path.read_text(encoding="utf-8"))
        self.ids = payload["ids"]
        self.documents = payload["documents"]
        self.embeddings = payload["embeddings"]
        self.metadatas = payload["metadatas"]
        return self

    def query(self, query_embedding: list[float], n_results: int = 8) -> dict:
        scored = []
        for i, emb in enumerate(self.embeddings):
            sim = cosine(query_embedding, emb)
            scored.append((sim, i))
        scored.sort(reverse=True)
        top = scored[:n_results]
        return {
            "documents": [[self.documents[i] for _, i in top]],
            "metadatas": [[self.metadatas[i] for _, i in top]],
            # distance = 1 - similarity (chroma-compatible-ish)
            "distances": [[1.0 - sim for sim, _ in top]],
        }


def get_store() -> VectorStore:
    return VectorStore().load()
