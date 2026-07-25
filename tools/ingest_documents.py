# Tool 3 - ingest_documents(ticker)
# architecture.md describes a full vector store built from years of annual
# reports, earnings call transcripts and investor presentations. inputs/ only
# actually contains one research report per company, but this still builds a
# real vector store (FAISS + local dense embeddings) rather than faking it -
# if more documents show up in inputs/ later, chunking/retrieval already
# scales to them without any code changes.

import json
from pathlib import Path

import faiss
import numpy as np
from fastembed import TextEmbedding
from pypdf import PdfReader

from config import INPUTS_DIR, VECTOR_STORE_DIR

# Free-tier LLM context windows are small, so cap how much text goes straight
# into a prompt (used by Agent 1's main analysis, which needs a holistic read).
MAX_CHARS_PER_DOCUMENT = 15000

# Chunking for the vector store - smaller windows than MAX_CHARS_PER_DOCUMENT
# since these get embedded and retrieved individually, not read end to end.
CHUNK_SIZE = 800
CHUNK_OVERLAP = 100

# Small local embedding model (runs via ONNX, no API key, no GPU needed) -
# downloaded and cached on first use.
EMBEDDING_MODEL = "BAAI/bge-small-en-v1.5"
DEFAULT_TOP_K = 5

_embedder = None


def find_documents(company: str) -> list[Path]:
    return [p for p in INPUTS_DIR.glob("*.pdf") if company.lower() in p.stem.lower()]


def _extract_raw_text(pdf_path: Path) -> str:
    reader = PdfReader(pdf_path)
    pages = [page.extract_text() or "" for page in reader.pages]
    return "\n".join(pages).strip()


def extract_text(pdf_path: Path) -> str:
    """Truncated text for direct prompting (Agent 1's main analysis call)."""
    text = _extract_raw_text(pdf_path)
    if len(text) > MAX_CHARS_PER_DOCUMENT:
        text = text[:MAX_CHARS_PER_DOCUMENT] + "\n\n[...document truncated for length...]"
    return text


def ingest(company: str) -> dict:
    """Return every document found for a company, each tagged with its source
    filename, plus one combined_text block for prompting the LLM."""
    documents = find_documents(company)
    if not documents:
        raise FileNotFoundError(f"No research documents found for '{company}' in {INPUTS_DIR}")

    sources = [{"source": path.name, "text": extract_text(path)} for path in documents]
    combined_text = "\n\n".join(f"--- SOURCE: {doc['source']} ---\n{doc['text']}" for doc in sources)

    return {"sources": sources, "combined_text": combined_text}


# --- Vector store (FAISS, dense retrieval) -------------------------------
# Used by tool 6 (verify_grounding) to pull only the passages relevant to a
# specific claim, instead of re-checking every claim against the whole report.


def _get_embedder() -> TextEmbedding:
    global _embedder
    if _embedder is None:
        _embedder = TextEmbedding(model_name=EMBEDDING_MODEL)
    return _embedder


def _chunk_text(text: str, source: str) -> list[dict]:
    chunks = []
    start = 0
    length = len(text)
    while start < length:
        end = start + CHUNK_SIZE
        chunk = text[start:end].strip()
        if chunk:
            chunks.append({"source": source, "text": chunk})
        if end >= length:
            break
        start = end - CHUNK_OVERLAP
    return chunks


def _index_paths(company: str) -> tuple[Path, Path]:
    stem = company.lower().strip()
    return VECTOR_STORE_DIR / f"{stem}.faiss", VECTOR_STORE_DIR / f"{stem}_chunks.json"


def _index_is_stale(index_path: Path, chunks_path: Path, documents: list[Path]) -> bool:
    if not index_path.exists() or not chunks_path.exists():
        return True
    index_mtime = index_path.stat().st_mtime
    return any(path.stat().st_mtime > index_mtime for path in documents)


def build_vector_index(company: str) -> None:
    """Chunk every document for a company, embed the chunks and write a FAISS
    index + a sidecar JSON of chunk text/sources to VECTOR_STORE_DIR."""
    documents = find_documents(company)
    if not documents:
        raise FileNotFoundError(f"No research documents found for '{company}' in {INPUTS_DIR}")

    chunks = []
    for path in documents:
        chunks.extend(_chunk_text(_extract_raw_text(path), path.name))
    if not chunks:
        raise ValueError(f"No extractable text found in documents for '{company}'")

    embedder = _get_embedder()
    vectors = np.array(list(embedder.embed([c["text"] for c in chunks])), dtype="float32")
    faiss.normalize_L2(vectors)

    index = faiss.IndexFlatIP(vectors.shape[1])
    index.add(vectors)

    VECTOR_STORE_DIR.mkdir(parents=True, exist_ok=True)
    index_path, chunks_path = _index_paths(company)
    faiss.write_index(index, str(index_path))
    chunks_path.write_text(json.dumps(chunks), encoding="utf-8")


def _load_vector_index(company: str) -> tuple[faiss.Index, list[dict]]:
    documents = find_documents(company)
    if not documents:
        raise FileNotFoundError(f"No research documents found for '{company}' in {INPUTS_DIR}")

    index_path, chunks_path = _index_paths(company)
    if _index_is_stale(index_path, chunks_path, documents):
        build_vector_index(company)

    index = faiss.read_index(str(index_path))
    chunks = json.loads(chunks_path.read_text(encoding="utf-8"))
    return index, chunks


def retrieve_relevant_chunks(company: str, query: str, top_k: int = DEFAULT_TOP_K) -> list[dict]:
    """Dense retrieval: embed the query, search the company's FAISS index, and
    return the top_k most similar chunks (each tagged with its source file)."""
    index, chunks = _load_vector_index(company)
    if not chunks:
        return []

    embedder = _get_embedder()
    query_vector = np.array(list(embedder.embed([query])), dtype="float32")
    faiss.normalize_L2(query_vector)

    k = min(top_k, len(chunks))
    _scores, indices = index.search(query_vector, k)
    return [chunks[i] for i in indices[0] if i != -1]

