# Moved into tools/ingest_documents.py (tool 3 from architecture.md).
# Kept as a thin re-export so nothing else breaks if it still points here.
from tools.ingest_documents import extract_text, find_documents, ingest

__all__ = ["find_documents", "extract_text", "ingest"]
