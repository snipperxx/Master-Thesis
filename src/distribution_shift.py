"""
Phase-4 — Conflict-distribution shift between two guideline versions.

Inputs: two `data/conflicts/<doc>...json` files (or already-loaded dicts).
Output: a per-label diff + summary suitable for direct rendering in
        the UI's "Compare with v2" panel.

The proposal calls out three deliverables explicitly:
  * conflict-type distribution shift  (per-label deltas — this module)
  * total conflict count reduction    (totals — derived here too)
  * layer-1 filter rate change        (how many pairs Layer-1 caught
                                       before LLM call — derived here)

Bar-chart-friendly JSON: { "labels": [...], "v1": [...], "v2": [...],
                           "delta": [...], "delta_pct": [...] }
"""

from __future__ import annotations

import json
from pathlib import Path


CONFLICT_LABELS = ("contradiction", "granularity", "redundancy", "unlabeled")


def _load(path_or_doc) -> dict:
    if isinstance(path_or_doc, (str, Path)):
        return json.loads(Path(path_or_doc).read_text(encoding="utf-8"))
    return path_or_doc


def _label_counts(doc: dict) -> dict[str, int]:
    """Read straight from the conflicts file. Fill in any missing labels."""
    counts = dict(doc.get("label_counts", {}))
    for lbl in CONFLICT_LABELS:
        counts.setdefault(lbl, 0)
    return counts


def _layer1_filter_rate(doc: dict) -> dict[str, float | int]:
    """How many matched pairs did Layer-1 resolve without calling Layer-2?

    A pair was "caught by Layer-1" iff its `layer1_reason` does NOT mention
    'escalate' and its conflict_label is not 'unlabeled' (orphan tag).
    layer2_calls is also persisted by run_phase2 for convenience.
    """
    pairs = doc.get("aligned_pairs", [])
    matched = [p for p in pairs if p.get("status") == "matched"]
    layer2_calls = int(doc.get("layer2_calls", 0))
    n_matched = len(matched) or 1
    return {
        "n_matched_pairs": len(matched),
        "n_layer2_calls": layer2_calls,
        "layer1_filter_rate": round(1 - layer2_calls / n_matched, 3),
    }


def compare_conflict_files(
    v1, v2, *,
    v1_label: str = "v1",
    v2_label: str = "v2",
) -> dict:
    """Diff two Phase-2 conflict files. v1, v2 may be dicts or paths."""
    a = _load(v1)
    b = _load(v2)
    c_a = _label_counts(a)
    c_b = _label_counts(b)

    labels = list(CONFLICT_LABELS)
    v1_counts = [c_a[l] for l in labels]
    v2_counts = [c_b[l] for l in labels]
    delta = [b_i - a_i for a_i, b_i in zip(v1_counts, v2_counts)]

    def _pct(a_i: int, b_i: int) -> float:
        return round((b_i - a_i) / a_i * 100, 1) if a_i else None  # type: ignore

    delta_pct = [_pct(a_i, b_i) for a_i, b_i in zip(v1_counts, v2_counts)]

    total_a = sum(v1_counts)
    total_b = sum(v2_counts)

    return {
        "v1_label": v1_label,
        "v2_label": v2_label,
        "labels": labels,
        "v1": v1_counts,
        "v2": v2_counts,
        "delta": delta,
        "delta_pct": delta_pct,
        "totals": {
            "v1": total_a,
            "v2": total_b,
            "delta": total_b - total_a,
            "delta_pct": _pct(total_a, total_b),
        },
        "layer1_rate": {
            "v1": _layer1_filter_rate(a),
            "v2": _layer1_filter_rate(b),
        },
    }


if __name__ == "__main__":
    import argparse, sys
    p = argparse.ArgumentParser(description="Compare two Phase-2 conflict files.")
    p.add_argument("--v1", required=True, type=Path)
    p.add_argument("--v2", required=True, type=Path)
    args = p.parse_args()
    out = compare_conflict_files(args.v1, args.v2)
    json.dump(out, sys.stdout, indent=2, ensure_ascii=False)
