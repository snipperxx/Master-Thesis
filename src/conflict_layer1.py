"""
Phase-2 — Step 2a: Cheap rule-based conflict labeller.

The Layer-1 filter exists to keep the Layer-2 LLM arbitrator off the
trivial cases. The proposal lists exactly the rules below; we keep the
parameters explicit and per-pair so they're tweakable from the VA tool
later (e.g. "what if I move REDUNDANCY's cosine cutoff from 0.95 to 0.92?").

Decision flow for a `matched` pair:

    cosine ≥ redundancy_cosine
    AND   subj+obj char overlap ≥ overlap_threshold
                 → REDUNDANCY  (filtered, no LLM call)

    numeric_value differs            → escalate (likely CONTRADICTION)
    polarity flips (not/no/never)    → escalate (antonym trap)
    otherwise                        → escalate

Orphan pairs (`unmatched_a` / `unmatched_b`) are left UNLABELED. They are
candidates for GRANULARITY at the document level (handled by Layer-2 in
the cluster context) or true coverage gaps; Layer-1 deliberately does not
guess.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from .alignment import AlignedPair

# ---------------------------------------------------------------------------
# Tunables (exposed via __init__ so the UI can override)
# ---------------------------------------------------------------------------


@dataclass
class Layer1Config:
    redundancy_cosine: float = 0.95     # very high → near-duplicate phrasing
    overlap_threshold: float = 0.75     # subject+object char-trigram Jaccard
    # Words whose presence on exactly one side flips the polarity of a claim.
    polarity_markers: tuple[str, ...] = (
        "not", "no", "never", "without", "fails", "failed", "unable",
    )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


_NUMBER_RE = re.compile(r"\d+[,.]?\d*")
_WORD_RE = re.compile(r"[A-Za-z]+")
# Legal-citation "No" — "Regulation (EEC) No 584/75", "No. 1418/76" — must NOT
# be read as the negation "no". Without this scrub, every fact that cites a
# regulation number trips the polarity-asymmetry escalation (observed on
# train-000014: gemma "security is ECU 20" vs phi4 "... No 584/75 shall be ...").
_CITATION_NO_RE = re.compile(r"\bno\.?\s*\d", re.IGNORECASE)


def _char_trigram_jaccard(a: str, b: str) -> float:
    """Char-trigram Jaccard on the concatenated subject+object — captures
    'same referent, same value' even when the function words differ.
    """

    def trigrams(s: str) -> set[str]:
        s = re.sub(r"\s+", " ", s).strip().lower()
        return {s[i : i + 3] for i in range(len(s) - 2)} if len(s) >= 3 else set()

    sa, sb = trigrams(a), trigrams(b)
    if not sa and not sb:
        return 1.0
    if not sa or not sb:
        return 0.0
    return len(sa & sb) / len(sa | sb)


def _numbers(s: str) -> list[str]:
    return _NUMBER_RE.findall(s or "")


def _polarity_tokens(s: str, markers: tuple[str, ...]) -> set[str]:
    s = _CITATION_NO_RE.sub(" ", s or "")
    return {w.lower() for w in _WORD_RE.findall(s) if w.lower() in markers}


def _so_text(fact: dict) -> str:
    """Subject + object — what we compare for REDUNDANCY overlap."""
    return f"{fact.get('subject','')} {fact.get('object','')}".strip()


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def classify_pair(pair: AlignedPair, cfg: Layer1Config | None = None) -> AlignedPair:
    """In-place: set `conflict_label` and `layer1_reason` on the pair.

    Returns the (mutated) pair so this composes naturally in a map.
    """
    cfg = cfg or Layer1Config()
    if pair.status != "matched":
        # Orphans are left for Layer-2 / cluster-level reasoning.
        pair.layer1_reason = pair.layer1_reason or f"orphan_{pair.status}"
        return pair

    fa, fb = pair.fact_a, pair.fact_b  # both non-None when matched
    assert fa is not None and fb is not None

    so_a, so_b = _so_text(fa), _so_text(fb)
    overlap = _char_trigram_jaccard(so_a, so_b)

    # ---- REDUNDANCY ---------------------------------------------------------
    if pair.cosine >= cfg.redundancy_cosine and overlap >= cfg.overlap_threshold:
        pair.conflict_label = "redundancy"
        pair.layer1_reason = (
            f"cos={pair.cosine:.3f}≥{cfg.redundancy_cosine}; "
            f"subj+obj trigram jaccard={overlap:.2f}≥{cfg.overlap_threshold}"
        )
        return pair

    # ---- Numeric divergence (escalate, hint CONTRADICTION) -----------------
    nums_a, nums_b = _numbers(so_a), _numbers(so_b)
    if nums_a and nums_b and set(nums_a) != set(nums_b):
        pair.conflict_label = "escalate"
        pair.layer1_reason = (
            f"numeric mismatch: A={nums_a}  B={nums_b}; cos={pair.cosine:.3f}"
        )
        return pair

    # ---- Polarity / antonym trap (escalate) --------------------------------
    pol_a = _polarity_tokens(fa.get("natural_language", ""), cfg.polarity_markers)
    pol_b = _polarity_tokens(fb.get("natural_language", ""), cfg.polarity_markers)
    if pol_a ^ pol_b:
        pair.conflict_label = "escalate"
        pair.layer1_reason = (
            f"polarity asymmetry: A={sorted(pol_a) or '∅'} B={sorted(pol_b) or '∅'}"
        )
        return pair

    # ---- Default: escalate to LLM ------------------------------------------
    pair.conflict_label = "escalate"
    pair.layer1_reason = (
        f"no rule fired; cos={pair.cosine:.3f} overlap={overlap:.2f}"
    )
    return pair


def classify_all(pairs: list[AlignedPair], cfg: Layer1Config | None = None) -> dict[str, int]:
    """Run `classify_pair` over every pair; return label counts."""
    counts: dict[str, int] = {}
    for p in pairs:
        classify_pair(p, cfg)
        counts[p.conflict_label] = counts.get(p.conflict_label, 0) + 1
    return counts
