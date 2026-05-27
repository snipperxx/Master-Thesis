"""
Phase-2 — Step 1: Cross-annotator fact alignment.

Pipeline contract:
    facts_per_annotator (dict[str, list[AtomicFact]])
      -> encode_facts (SBERT or TF-IDF fallback)
      -> pairwise Hungarian assignment
      -> list[AlignedPair]

Encoder backend
---------------
SBERT (`all-MiniLM-L6-v2`) is the default. When `import torch` or
`sentence_transformers` fail, we fall back to sklearn char-trigram TF-IDF
so the rest of the pipeline stays testable in environments that can't
install torch (notably the Cowork Linux sandbox). Both backends produce
L2-normalised vectors so `A @ B.T` is cosine either way.

For TF-IDF correctness the two sides of any pair must share a vocabulary.
We achieve that with `encode_facts_joint(*lists)` which fits one
TfidfVectorizer on the union and returns per-list row slices. `align_two`
and `align_all_pairs` both go through `encode_facts_joint` so they stay
consistent.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

import numpy as np

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Data
# ---------------------------------------------------------------------------


@dataclass
class AlignedPair:
    """One Hungarian assignment between two annotators (or an orphan)."""

    annotator_a: str
    annotator_b: str
    fact_a: dict | None
    fact_b: dict | None
    cosine: float
    status: str           # "matched" | "unmatched_a" | "unmatched_b"
    conflict_label: str = "unlabeled"
    layer1_reason: str | None = None
    layer2_reason: str | None = None


# ---------------------------------------------------------------------------
# Encoder backend resolution
# ---------------------------------------------------------------------------


_SBERT_MODEL_NAME = "sentence-transformers/all-MiniLM-L6-v2"
_BACKEND: str | None = None
_encoder = None


def _fact_text(fact: dict) -> str:
    nl = fact.get("natural_language")
    if nl and nl.strip():
        return nl.strip()
    return f"{fact.get('subject','')} {fact.get('predicate','')} {fact.get('object','')}".strip()


def _resolve_backend() -> str:
    global _BACKEND
    if _BACKEND is not None:
        return _BACKEND
    try:
        import torch  # noqa: F401
        from sentence_transformers import SentenceTransformer  # noqa: F401

        _BACKEND = "sbert"
        logger.info("Alignment backend: SBERT (%s)", _SBERT_MODEL_NAME)
    except Exception as exc:
        _BACKEND = "tfidf"
        logger.warning(
            "Alignment backend: TF-IDF fallback (sentence-transformers unavailable: %s)",
            exc.__class__.__name__,
        )
    return _BACKEND


def _get_sbert():
    global _encoder
    if _encoder is None:
        from sentence_transformers import SentenceTransformer
        _encoder = SentenceTransformer(_SBERT_MODEL_NAME, device="cpu")
    return _encoder


def _encode_sbert(texts: list[str]) -> np.ndarray:
    enc = _get_sbert()
    emb = enc.encode(texts, normalize_embeddings=True,
                     convert_to_numpy=True, show_progress_bar=False)
    return emb.astype(np.float32, copy=False)


def _encode_tfidf(texts: list[str]) -> np.ndarray:
    from sklearn.feature_extraction.text import TfidfVectorizer
    from sklearn.preprocessing import normalize

    vec = TfidfVectorizer(analyzer="char_wb", ngram_range=(3, 5),
                          min_df=1, sublinear_tf=True)
    mat = vec.fit_transform(texts)
    mat = normalize(mat, norm="l2", axis=1, copy=False)
    return mat.toarray().astype(np.float32, copy=False)


# ---------------------------------------------------------------------------
# Public encoding API
# ---------------------------------------------------------------------------


def encode_facts(facts: list[dict]) -> np.ndarray:
    """Returns (N, D) L2-normalised float32 embeddings, or (0, 384) when empty."""
    if not facts:
        return np.zeros((0, 384), dtype=np.float32)
    texts = [_fact_text(f) for f in facts]
    backend = _resolve_backend()
    return _encode_sbert(texts) if backend == "sbert" else _encode_tfidf(texts)


def encode_facts_joint(*fact_lists: list[dict]) -> list[np.ndarray]:
    """Encode multiple lists under one (shared-vocabulary) fit.

    SBERT doesn't need a shared vocabulary, but TF-IDF does — `A @ B.T`
    only makes sense when columns are the same features. Calling this
    helper is always safe; under SBERT it degenerates to per-list encoding.
    """
    backend = _resolve_backend()
    if backend == "sbert":
        return [encode_facts(fl) for fl in fact_lists]

    from sklearn.feature_extraction.text import TfidfVectorizer
    from sklearn.preprocessing import normalize

    all_texts: list[str] = []
    slices: list[tuple[int, int]] = []
    for fl in fact_lists:
        start = len(all_texts)
        all_texts.extend(_fact_text(f) for f in fl)
        slices.append((start, len(all_texts)))
    if not all_texts:
        return [np.zeros((0, 1), dtype=np.float32) for _ in fact_lists]

    vec = TfidfVectorizer(analyzer="char_wb", ngram_range=(3, 5),
                          min_df=1, sublinear_tf=True)
    mat = vec.fit_transform(all_texts)
    mat = normalize(mat, norm="l2", axis=1, copy=False).toarray().astype(np.float32, copy=False)
    return [mat[s:e] for (s, e) in slices]


# ---------------------------------------------------------------------------
# Hungarian alignment
# ---------------------------------------------------------------------------


def align_two(
    facts_a: list[dict],
    facts_b: list[dict],
    *,
    annotator_a: str,
    annotator_b: str,
    threshold: float = 0.78,
    emb_a: np.ndarray | None = None,
    emb_b: np.ndarray | None = None,
) -> list[AlignedPair]:
    """Align two annotators' facts one-to-one above `threshold`."""
    from scipy.optimize import linear_sum_assignment

    if not facts_a and not facts_b:
        return []
    if not facts_a:
        return [
            AlignedPair(annotator_a, annotator_b, None, fb, 0.0, "unmatched_b")
            for fb in facts_b
        ]
    if not facts_b:
        return [
            AlignedPair(annotator_a, annotator_b, fa, None, 0.0, "unmatched_a")
            for fa in facts_a
        ]

    # Joint encoding ensures TF-IDF backend has a shared vocab.
    if emb_a is None or emb_b is None:
        joint = encode_facts_joint(facts_a, facts_b)
        if emb_a is None:
            emb_a = joint[0]
        if emb_b is None:
            emb_b = joint[1]

    cos = emb_a @ emb_b.T
    cost = 1.0 - cos
    rows_a, cols_b = linear_sum_assignment(cost)

    matched_a: set[int] = set()
    matched_b: set[int] = set()
    pairs: list[AlignedPair] = []

    for ia, ib in zip(rows_a, cols_b):
        sim = float(cos[ia, ib])
        if sim >= threshold:
            pairs.append(AlignedPair(
                annotator_a=annotator_a, annotator_b=annotator_b,
                fact_a=facts_a[ia], fact_b=facts_b[ib],
                cosine=sim, status="matched",
            ))
            matched_a.add(ia)
            matched_b.add(ib)

    for ia, fa in enumerate(facts_a):
        if ia not in matched_a:
            best_b = int(np.argmax(cos[ia])) if cos.shape[1] else -1
            best_sim = float(cos[ia, best_b]) if best_b >= 0 else 0.0
            pairs.append(AlignedPair(
                annotator_a=annotator_a, annotator_b=annotator_b,
                fact_a=fa, fact_b=None, cosine=best_sim,
                status="unmatched_a",
                layer1_reason=f"best partner cosine={best_sim:.3f} < {threshold}",
            ))

    for ib, fb in enumerate(facts_b):
        if ib not in matched_b:
            best_a = int(np.argmax(cos[:, ib])) if cos.shape[0] else -1
            best_sim = float(cos[best_a, ib]) if best_a >= 0 else 0.0
            pairs.append(AlignedPair(
                annotator_a=annotator_a, annotator_b=annotator_b,
                fact_a=None, fact_b=fb, cosine=best_sim,
                status="unmatched_b",
                layer1_reason=f"best partner cosine={best_sim:.3f} < {threshold}",
            ))

    return pairs


def align_all_pairs(
    facts_per_annotator: dict[str, list[dict]],
    *,
    threshold: float = 0.78,
) -> list[AlignedPair]:
    """Run Hungarian alignment across every unordered annotator pair."""
    annotators = list(facts_per_annotator.keys())
    embs = encode_facts_joint(*[facts_per_annotator[a] for a in annotators])
    emb_cache = dict(zip(annotators, embs))

    all_pairs: list[AlignedPair] = []
    for i in range(len(annotators)):
        for j in range(i + 1, len(annotators)):
            ann_a, ann_b = annotators[i], annotators[j]
            pairs = align_two(
                facts_per_annotator[ann_a],
                facts_per_annotator[ann_b],
                annotator_a=ann_a, annotator_b=ann_b,
                threshold=threshold,
                emb_a=emb_cache[ann_a], emb_b=emb_cache[ann_b],
            )
            all_pairs.extend(pairs)
    return all_pairs


def pair_to_dict(p: AlignedPair) -> dict:
    return {
        "annotator_a": p.annotator_a,
        "annotator_b": p.annotator_b,
        "fact_a_id": p.fact_a["fact_id"] if p.fact_a else None,
        "fact_b_id": p.fact_b["fact_id"] if p.fact_b else None,
        "cosine": round(p.cosine, 4),
        "status": p.status,
        "conflict_label": p.conflict_label,
        "layer1_reason": p.layer1_reason,
        "layer2_reason": p.layer2_reason,
    }
