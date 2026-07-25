# Moved into tools/verify_grounding.py (tool 6 from architecture.md).
# Kept as a thin re-export so nothing else breaks if it still points here.
from tools.verify_grounding import check_claims, summarize_confidence

__all__ = ["check_claims", "summarize_confidence"]
