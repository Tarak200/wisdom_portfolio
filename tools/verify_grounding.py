# Tool 6 - verify_grounding(claims, document_store, quant_data)
# Separate LLM call from Agent 1's own, so the model isn't just agreeing with
# itself for free. For each claim, tool 3's FAISS index is searched for the
# passages most relevant to that specific claim (dense retrieval), and only
# those passages - not the whole report - are shown to the fact-checker,
# alongside the fetched fundamentals as ground truth for anything numeric.

import json

from llm_client import AllModelsFailedError, chat
from tools import ingest_documents

VERIFY_PROMPT = """You are a fact-checker. Below is a list of claims someone made,
each with a citation and the passages retrieved from the source documents for
that specific claim (dense retrieval over a FAISS vector index - only the
passages most relevant to each claim are shown for it, not the whole report).

For every claim, check whether ITS retrieved passages actually support it. Reply
with "Verified" if it's backed up, "Contradicted" if the passages say something
different, or "Unverified" if the passages shown don't give enough information
to check it either way. Do not be lenient - if you can't find clear support in
the passages shown for that claim, it is Unverified, not Verified.

Reply with ONLY a JSON array, no other text, in this exact shape:
[{{"claim": "...", "citation": "...", "status": "Verified|Unverified|Contradicted", "note": "one short sentence"}}]

FETCHED MARKET FUNDAMENTALS (quant_data - treat as ground truth for anything numeric):
{quant_data_json}

CLAIMS WITH THEIR RETRIEVED EVIDENCE:
{claims_with_evidence_json}
"""


def check_claims(
    claims: list[dict], company: str, quant_data: dict | None = None, top_k: int = ingest_documents.DEFAULT_TOP_K
) -> list[dict]:
    """Verify each {claim, citation} pair against passages retrieved from that
    company's FAISS index (top_k per claim) plus quant_data. Returns the same
    list of claims with 'status' and 'note' added to each item."""
    if not claims:
        return []

    claims_with_evidence = []
    for claim in claims:
        query = f"{claim.get('claim', '')} {claim.get('citation', '')}".strip()
        try:
            evidence = ingest_documents.retrieve_relevant_chunks(company, query, top_k=top_k)
        except FileNotFoundError:
            evidence = []
        claims_with_evidence.append({**claim, "evidence": evidence})

    prompt = VERIFY_PROMPT.format(
        quant_data_json=json.dumps(quant_data or {}, indent=2, default=str),
        claims_with_evidence_json=json.dumps(claims_with_evidence, indent=2),
    )

    def _is_valid_verification(text: str) -> bool:
        """Valid JSON array AND the same length as claims sent - a mismatched
        count means the claim-to-status mapping downstream (zip-by-position)
        would silently be wrong, which is worse than no verification at all."""
        parsed = _parse_json_array(text)
        return parsed is not None and len(parsed) == len(claims)

    try:
        raw_reply = chat([{"role": "user", "content": prompt}], validate=_is_valid_verification)
    except AllModelsFailedError:
        # Every model in the chain either errored or returned something that
        # didn't parse / didn't match the claim count - don't silently pretend
        # any of it passed.
        return [
            {**c, "status": "Unverified", "note": "grounding check failed across all models (invalid or mismatched response)"}
            for c in claims
        ]

    verified = _parse_json_array(raw_reply)
    if verified is None or len(verified) != len(claims):
        return [{**c, "status": "Unverified", "note": "grounding check failed to return valid JSON"} for c in claims]

    return verified


def _parse_json_array(raw_text: str) -> list[dict] | None:
    cleaned = raw_text.strip().removeprefix("```json").removeprefix("```").removesuffix("```").strip()
    try:
        parsed = json.loads(cleaned)
        return parsed if isinstance(parsed, list) else None
    except json.JSONDecodeError:
        return None


def summarize_confidence(verified_claims: list[dict]) -> dict:
    """Overall confidence = fraction of claims that came back Verified."""
    if not verified_claims:
        return {"verified": 0, "total": 0, "confidence_pct": None}

    verified_count = sum(1 for c in verified_claims if c.get("status") == "Verified")
    total = len(verified_claims)
    return {"verified": verified_count, "total": total, "confidence_pct": round(100 * verified_count / total, 1)}


def compute_hallucination_score(verified_claims: list[dict]) -> dict:
    """0.0 = fully grounded (every claim Verified), 1.0 = fully hallucinated
    (every claim Contradicted). Contradicted claims count fully against
    groundedness since the source material actively disagrees; Unverified
    claims count at half weight - that's a gap in evidence, not proof the
    claim is fabricated, but it still isn't clean support either.
    No claims cited at all is treated as maximally ungrounded (a recommendation
    with zero citations is exactly what tool 6 exists to catch)."""
    if not verified_claims:
        return {"score": 1.0, "contradicted": 0, "unverified": 0, "verified": 0, "total": 0}

    contradicted = sum(1 for c in verified_claims if c.get("status") == "Contradicted")
    unverified = sum(1 for c in verified_claims if c.get("status") == "Unverified")
    verified = sum(1 for c in verified_claims if c.get("status") == "Verified")
    total = len(verified_claims)

    score = (contradicted * 1.0 + unverified * 0.5) / total
    return {"score": round(score, 4), "contradicted": contradicted, "unverified": unverified, "verified": verified, "total": total}
