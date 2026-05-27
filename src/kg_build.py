"""
Phase-2/3 — Knowledge graph builder.

Reads the Phase-2 output (`data/conflicts/<doc_id>.json`) and produces a
Cytoscape.js-shaped JSON: { "nodes": [...], "edges": [...] }.

Conventions
-----------
* One **node** per entity cluster (subject-or-object surface forms that
  the SBERT/TF-IDF clusterer collapsed together at the configured
  threshold). Node `id` = cluster_id; node `label` = the shortest member
  surface form; node `data.surface_forms` lists every member with its
  annotator(s) so the UI can show "Council / Council of the EU / EC
  Council — 3 surface forms, 3 annotators".
* One **edge** per (s-cluster, predicate-string, o-cluster) triple.
  Multiple annotators producing the same triple collapse into one edge;
  their fact_ids accumulate in `edge.data.fact_ids` and their annotators
  in `edge.data.annotators`. This is what surfaces REDUNDANCY visually:
  one edge with three annotator chips, all agreeing.
* Conflict label on an edge = the **strongest** conflict observed on any
  AlignedPair whose two facts both ride this edge. Severity order:
        CONTRADICTION > GRANULARITY > REDUNDANCY > NO_CONFLICT > UNLABELED
  This is the rule the proposal calls "three-level node colouring" but
  applied at the edge — colouring the *relation* is what tells the
  analyst where to look. Node colour is derived from incident edges.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Iterable


_SEVERITY = {
    "contradiction": 4,
    "granularity": 3,
    "redundancy": 2,
    "no_conflict": 1,
    "unlabeled": 0,
    "escalate": 0,
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _norm_predicate(pred: str) -> str:
    """Cheap predicate normalisation — strip determiners, lowercase.

    Phase-3 entity resolution does the heavy lifting on nodes; here we
    just want "issued" and "issued" not "issued" vs "Issued ". A more
    aggressive predicate merge (e.g. lemmatisation) belongs in a later
    iteration with proper evaluation.
    """
    return " ".join((pred or "").lower().split())


def _strongest_label(labels: Iterable[str]) -> str:
    best, best_sev = "unlabeled", -1
    for lbl in labels:
        sev = _SEVERITY.get(lbl, -1)
        if sev > best_sev:
            best, best_sev = lbl, sev
    return best


# ---------------------------------------------------------------------------
# Build
# ---------------------------------------------------------------------------


def build_graph(conflicts_doc: dict) -> dict:
    """Translate a Phase-2 output document into Cytoscape elements."""
    surface_to_cluster: dict[str, str] = conflicts_doc["entity_clusters"]
    facts_per_ann: dict[str, list[dict]] = conflicts_doc["facts_per_annotator"]
    aligned_pairs = conflicts_doc.get("aligned_pairs", [])

    # 1. Build a fact-id -> fact lookup, and accumulate which annotators
    #    each cluster sees.
    fact_by_id: dict[str, dict] = {}
    cluster_members: dict[str, dict[str, set[str]]] = {}
    # cluster_id -> {"surface_forms": set, "annotators": set}

    def _bump_cluster(cluster: str, surface: str, annotator: str) -> None:
        slot = cluster_members.setdefault(cluster, {"surface_forms": set(), "annotators": set()})
        slot["surface_forms"].add(surface)
        slot["annotators"].add(annotator)

    for annotator, facts in facts_per_ann.items():
        for f in facts:
            fact_by_id[f["fact_id"]] = f
            for k in ("subject", "object"):
                surface = (f.get(k) or "").strip()
                if not surface or surface.lower() == "null":
                    continue
                cluster = surface_to_cluster.get(surface)
                if cluster is None:
                    continue
                _bump_cluster(cluster, surface, annotator)

    # 2. Build the edge index keyed by (s_cluster, predicate, o_cluster).
    edges: dict[tuple[str, str, str], dict] = {}

    def _edge_for(f: dict) -> tuple[str, str, str] | None:
        subj = (f.get("subject") or "").strip()
        obj = (f.get("object") or "").strip()
        if not subj or not obj or obj.lower() == "null":
            return None
        s_c = surface_to_cluster.get(subj)
        o_c = surface_to_cluster.get(obj)
        if not s_c or not o_c:
            return None
        return (s_c, _norm_predicate(f.get("predicate", "")), o_c)

    for annotator, facts in facts_per_ann.items():
        for f in facts:
            key = _edge_for(f)
            if key is None:
                continue
            slot = edges.setdefault(
                key,
                {
                    "fact_ids": set(),
                    "annotators": set(),
                    "conflict_labels": set(),
                    "section_paths": set(),
                },
            )
            slot["fact_ids"].add(f["fact_id"])
            slot["annotators"].add(annotator)
            loc = f.get("source_locator") or {}
            sp = loc.get("section_path")
            if sp:
                slot["section_paths"].add(sp)

    # 3. Attach conflict labels: walk aligned_pairs, find each pair's
    #    edge (both sides should hit the *same* edge under REDUNDANCY /
    #    CONTRADICTION; under GRANULARITY they may diverge — in that case
    #    we mark the A-edge with GRANULARITY because the divergence is
    #    visible there).
    for pair in aligned_pairs:
        label = pair.get("conflict_label", "unlabeled")
        if label in ("unlabeled", "escalate"):
            continue
        fa = fact_by_id.get(pair.get("fact_a_id") or "")
        fb = fact_by_id.get(pair.get("fact_b_id") or "")
        for f in (fa, fb):
            if not f:
                continue
            key = _edge_for(f)
            if key in edges:
                edges[key]["conflict_labels"].add(label)

    # 4. Serialize. Strongest label dictates colour; counts decide width.
    cy_nodes: list[dict] = []
    node_label_severity: dict[str, str] = {}
    for cid, info in cluster_members.items():
        cy_nodes.append(
            {
                "data": {
                    "id": cid,
                    "label": min(info["surface_forms"], key=lambda s: (len(s), s)),
                    "surface_forms": sorted(info["surface_forms"]),
                    "annotators": sorted(info["annotators"]),
                    "n_surface": len(info["surface_forms"]),
                    "n_annotators": len(info["annotators"]),
                    "conflict_label": "unlabeled",  # filled in below
                }
            }
        )

    cy_edges: list[dict] = []
    for (s_c, pred, o_c), info in edges.items():
        label = _strongest_label(info["conflict_labels"]) if info["conflict_labels"] else "unlabeled"
        cy_edges.append(
            {
                "data": {
                    "id": f"e_{s_c}__{pred[:24]}__{o_c}".replace(" ", "_"),
                    "source": s_c,
                    "target": o_c,
                    "label": pred,
                    "annotators": sorted(info["annotators"]),
                    "fact_ids": sorted(info["fact_ids"]),
                    "section_paths": sorted(info["section_paths"]),
                    "conflict_label": label,
                    "n_annotators": len(info["annotators"]),
                }
            }
        )
        # Propagate severity to incident nodes
        sev = _SEVERITY.get(label, 0)
        for endpoint in (s_c, o_c):
            cur = node_label_severity.get(endpoint, "unlabeled")
            if sev > _SEVERITY.get(cur, 0):
                node_label_severity[endpoint] = label

    for node in cy_nodes:
        nid = node["data"]["id"]
        node["data"]["conflict_label"] = node_label_severity.get(nid, "unlabeled")

    return {
        "doc_id": conflicts_doc["doc_id"],
        "params": conflicts_doc.get("params", {}),
        "nodes": cy_nodes,
        "edges": cy_edges,
        "summary": {
            "n_nodes": len(cy_nodes),
            "n_edges": len(cy_edges),
            "edge_label_counts": _count_by_label(cy_edges),
        },
    }


def _count_by_label(edges: list[dict]) -> dict[str, int]:
    out: dict[str, int] = {}
    for e in edges:
        lbl = e["data"]["conflict_label"]
        out[lbl] = out.get(lbl, 0) + 1
    return out


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main() -> None:
    import argparse

    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--in", dest="inp", type=Path, default=Path("data/conflicts/train-000000.json"))
    p.add_argument("--out", type=Path, default=None,
                   help="Default: data/graphs/<doc_id>.json")
    args = p.parse_args()

    doc = json.loads(args.inp.read_text(encoding="utf-8"))
    graph = build_graph(doc)

    out = args.out or Path("data/graphs") / f"{doc['doc_id']}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(graph, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[kg_build] {graph['summary']}")
    print(f"[kg_build] wrote {out}")


if __name__ == "__main__":
    main()
