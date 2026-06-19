"""
Qdrant vector memory for approved Pikorua campaigns — Task 2.3.

Uses Qdrant in local (on-disk) mode — no server required.
Data is persisted to outputs/qdrant_db/ alongside other run outputs.

Schema per record:
  - id: run_id (string, hashed to uint64)
  - vector: fastembed embedding of the combined brief + copy text
  - payload:
      run_id, property_name, city, locality, property_type, price_cr,
      buyer_type, goal, meta_headlines (list), meta_bodies (list),
      copy_scorecard_summary, approved_at

Seeding: call store_approved_campaign() after a run is approved.
Retrieval: call retrieve_similar_campaigns() to get top-K similar past campaigns
           as few-shot context for the copywriter.
"""

import hashlib
import json
import pathlib
from datetime import datetime, timezone
from typing import Optional

from qdrant_client import QdrantClient
from qdrant_client.models import Distance, PointStruct, VectorParams

_COLLECTION = "approved_campaigns"
_DB_PATH = str(
    pathlib.Path(__file__).parent.parent.parent.parent / "outputs" / "qdrant_db"
)


def _client() -> QdrantClient:
    return QdrantClient(path=_DB_PATH)


def _ensure_collection(client: QdrantClient) -> None:
    existing = [c.name for c in client.get_collections().collections]
    if _COLLECTION not in existing:
        # fastembed default model: BAAI/bge-small-en-v1.5 → 384 dims
        client.create_collection(
            collection_name=_COLLECTION,
            vectors_config=VectorParams(size=384, distance=Distance.COSINE),
        )


def _run_id_to_point_id(run_id: str) -> int:
    return int(hashlib.md5(run_id.encode()).hexdigest(), 16) % (2**63)


def _embed(text: str) -> list[float]:
    from fastembed import TextEmbedding
    try:
        model = TextEmbedding()  # default: BAAI/bge-small-en-v1.5, 384 dims
        embeddings = list(model.embed([text]))
        return embeddings[0].tolist()
    except Exception as exc:
        # Model file missing or corrupt (e.g. interrupted download).
        # Clear the broken cache entry so the next call re-downloads it.
        import shutil, os
        cache_root = os.path.join(
            os.environ.get("TEMP", os.path.expanduser("~")),
            "fastembed_cache",
        )
        broken = os.path.join(cache_root, "models--qdrant--bge-small-en-v1.5-onnx-q")
        if os.path.exists(broken):
            try:
                shutil.rmtree(broken)
            except Exception:
                pass
        raise RuntimeError(
            f"Embedding model unavailable ({exc}). "
            "Deleted stale cache — approve the campaign again to retry."
        ) from exc


def store_approved_campaign(
    run_id: str,
    brief: dict,
    review_folder: pathlib.Path,
    scorecard_summary: Optional[str] = None,
) -> bool:
    """
    Read the ad_copy.md and copy_scorecard.md from the review folder,
    build an embedding from brief + copy text, and upsert into Qdrant.

    Returns True on success, False if required files are missing.
    """
    copy_path = review_folder / "ad_copy.md"
    if not copy_path.exists():
        return False

    copy_text = copy_path.read_text(encoding="utf-8")

    # Build the text to embed: brief summary + copy
    embed_text = (
        f"Property: {brief.get('property_name', '')} "
        f"{brief.get('property_type', '')} in {brief.get('city', '')} "
        f"{brief.get('locality', '')} at ₹{brief.get('price_cr', '')} Cr. "
        f"Buyer: {brief.get('buyer_type', '')}. Goal: {brief.get('goal', '')}.\n\n"
        f"{copy_text[:2000]}"  # cap to avoid huge embeddings
    )

    # Extract Meta headlines/bodies from copy text (best-effort — lines starting with "Headline:")
    meta_headlines = [
        l.split(":", 1)[1].strip()
        for l in copy_text.splitlines()
        if l.strip().lower().startswith("headline:")
    ][:5]
    meta_bodies = [
        l.split(":", 1)[1].strip()
        for l in copy_text.splitlines()
        if l.strip().lower().startswith("body:")
    ][:5]

    payload = {
        "run_id": run_id,
        "property_name": brief.get("property_name", ""),
        "city": brief.get("city", ""),
        "locality": brief.get("locality", ""),
        "property_type": brief.get("property_type", ""),
        "price_cr": str(brief.get("price_cr", "")),
        "buyer_type": brief.get("buyer_type", ""),
        "goal": brief.get("goal", ""),
        "meta_headlines": meta_headlines,
        "meta_bodies": meta_bodies,
        "copy_scorecard_summary": scorecard_summary or "",
        "approved_at": datetime.now(timezone.utc).isoformat(),
        "review_folder": str(review_folder),
    }

    client = _client()
    _ensure_collection(client)
    vector = _embed(embed_text)

    client.upsert(
        collection_name=_COLLECTION,
        points=[
            PointStruct(
                id=_run_id_to_point_id(run_id),
                vector=vector,
                payload=payload,
            )
        ],
    )
    return True


def retrieve_similar_campaigns(
    property_name: str,
    property_type: str,
    city: str,
    buyer_type: str,
    goal: str,
    top_k: int = 3,
) -> list[dict]:
    """
    Return top_k approved campaigns most similar to the given brief.
    Returns [] if the collection is empty or doesn't exist yet.
    """
    client = _client()
    existing = [c.name for c in client.get_collections().collections]
    if _COLLECTION not in existing:
        return []

    count = client.count(collection_name=_COLLECTION).count
    if count == 0:
        return []

    query_text = (
        f"Property: {property_name} {property_type} in {city}. "
        f"Buyer: {buyer_type}. Goal: {goal}."
    )
    vector = _embed(query_text)

    results = client.search(
        collection_name=_COLLECTION,
        query_vector=vector,
        limit=min(top_k, count),
        with_payload=True,
    )
    return [r.payload for r in results]


def format_as_fewshot(campaigns: list[dict]) -> str:
    """
    Format retrieved campaigns as a few-shot block for injection into prompts.
    Returns empty string if no campaigns.
    """
    if not campaigns:
        return ""

    lines = ["APPROVED CAMPAIGN EXAMPLES (from past Pikorua runs):\n"]
    for i, c in enumerate(campaigns, 1):
        lines.append(f"Example {i}: {c.get('property_name')} — {c.get('city')}")
        lines.append(f"  Buyer: {c.get('buyer_type')} | Goal: {c.get('goal')}")
        for h, b in zip(c.get("meta_headlines", []), c.get("meta_bodies", [])):
            lines.append(f"  Headline: {h}")
            lines.append(f"  Body:     {b}")
        lines.append(f"  Result: {c.get('copy_scorecard_summary', 'approved')}\n")

    return "\n".join(lines)
