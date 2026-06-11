"""
Phase-2 batch driver — wires alignment, Layer-1 filter, optional Layer-2
arbitration, and SBERT-based entity clustering into a single command.

Usage
-----
    # Single doc, skip the LLM (use a deterministic stub instead):
    python -m scripts.run_phase2 --doc train-000000 --skip-layer2

    # All docs with >=2 annotators on disk (real Phase-1 output or synthetic):
    python -m scripts.run_phase2 --all --skip-layer2

    # Full run (requires Ollama running locally with qwen3.5:4b loaded):
    python -m scripts.run_phase2 --doc train-000000 --layer2-model qwen3.5:4b

Inputs:   data/facts/<annotator>/<doc_id>.json   (one file per annotator)
Outputs:  data/conflicts/<doc_id>.json           (everything the UI needs)
"""

from __future__ import annotations

import argparse
import json
import re
from collections import defaultdict
from pathlib import Path

from src.alignment import align_all_pairs, pair_to_dict, encode_facts_joint
from src.conflict_layer1 import Layer1Config, classify_all
from src.conflict_layer2 import arbitrate_all


def discover_annotators(facts_root: Path, doc_id: str) -> dict:
    out: dict[str, list[dict]] = {}
    for sub in sorted(facts_root.iterdir()):
        if not sub.is_dir():
            continue
        fp = sub / f"{doc_id}.json"
        if not fp.exists():
            continue
        try:
            doc = json.loads(fp.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            continue
        annotator = doc.get("annotator") or sub.name
        out[annotator] = doc.get("facts", [])
    return out


def discover_all_doc_ids(facts_root: Path) -> list:
    """Doc ids that have at least 2 annotators with facts on disk."""
    by_doc: dict[str, set[str]] = {}
    if not facts_root.exists():
        return []
    for sub in sorted(facts_root.iterdir()):
        if not sub.is_dir():
            continue
        for fp in sub.glob("*.json"):
            by_doc.setdefault(fp.stem, set()).add(sub.name)
    return sorted(d for d, a in by_doc.items() if len(a) >= 2)


_DET_RE = re.compile(r"^(?:the|a|an|this|that|these|those|its|their)\s+", re.I)
_DATE_TAIL_RE = re.compile(
    r"\s*(?:of \d{1,2} \w+ \d{4}|in (?:19|20)\d{2}|on \d{1,2} \w+ \d{4}|\((?:19|20)\d{2}\))", re.I)
_PREPS = {"of", "in", "on", "to", "for", "by", "with", "under", "at", "from",
          "concerning", "regarding"}


def normalize_entity(s: str, max_tokens: int = 6) -> str:
    """Reduce a (possibly heavily modified) entity mention to its core noun
    phrase for CLUSTERING purposes — poster-style nodes ("deficit", "Council
    Recommendation") instead of fully decontextualized strings ("general
    government deficit of the Netherlands in 2004").

    The decontextualized surface stays on the fact and in the node's
    surface_forms; only the merge key is normalized. This resolves the
    tension between guideline §2/§3 (minimum-sufficient modifiers, needed
    for fact self-containedness) and KG mergeability: modifiers make
    entities unique strings that never cluster, fragmenting the graph.
    """
    t = _DATE_TAIL_RE.sub("", (s or "").strip())
    t = _DET_RE.sub("", t)
    toks = t.split()
    if len(toks) > max_tokens:
        for i in range(2, len(toks)):
            if toks[i].lower() in _PREPS:
                toks = toks[:i]
                break
        toks = toks[:max_tokens + 2]
    t = " ".join(toks).strip(" ,;:.")
    return t or (s or "").strip()


def cluster_entities(facts_per_annotator: dict, *, merge_threshold: float = 0.78,
                     core_entities: bool = False) -> dict:
    surface_forms: list[str] = []
    seen: set[str] = set()
    for facts in facts_per_annotator.values():
        for f in facts:
            for k in ("subject", "object"):
                s = (f.get(k) or "").strip()
                if not s or s.lower() == "null" or s in seen:
                    continue
                seen.add(s)
                surface_forms.append(s)

    if not surface_forms:
        return {}

    keys = [normalize_entity(s) for s in surface_forms] if core_entities else surface_forms
    sham = [{"natural_language": k} for k in keys]
    [emb] = encode_facts_joint(sham)
    sims = emb @ emb.T

    n = len(surface_forms)
    parent = list(range(n))

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(a, b):
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[ra] = rb

    for i in range(n):
        for j in range(i + 1, n):
            if sims[i, j] >= merge_threshold:
                union(i, j)

    cluster_members: dict[int, list[int]] = defaultdict(list)
    for i in range(n):
        cluster_members[find(i)].append(i)

    surface_to_cluster: dict[str, str] = {}
    for root, members in cluster_members.items():
        rep = min((surface_forms[i] for i in members), key=lambda s: (len(s), s))
        cluster_id = "ent_" + re.sub(r"[^a-z0-9]+", "_", rep.lower())[:24].strip("_") + f"_{root:03d}"
        for m in members:
            surface_to_cluster[surface_forms[m]] = cluster_id
    return surface_to_cluster


_NUM_RE = re.compile(r"\d+[,.]?\d+\s*%")


def _stub_layer2(prompt: str, **_kw) -> str:
    nums = set(_NUM_RE.findall(prompt))
    if len(nums) >= 2:
        label = "CONTRADICTION"
    elif "split" in prompt.lower() or "qualified" in prompt.lower():
        label = "GRANULARITY"
    else:
        label = "REDUNDANCY"
    return json.dumps({"label": label, "reason": f"stub:{label.lower()}"})


def run(doc_id, *, facts_root, parsed_root, out_root, align_threshold,
        redundancy_cosine, merge_threshold, layer2_model, skip_layer2, layer2_url,
        out_suffix: str = ""):
    facts_per_annotator = discover_annotators(facts_root, doc_id)
    if not facts_per_annotator:
        raise SystemExit(f"No annotators found under {facts_root} for doc {doc_id!r}")
    if len(facts_per_annotator) < 2:
        # A 1-annotator "comparison" has zero aligned pairs and would fabricate
        # a perfect-convergence result (observed when one extraction cell of a
        # pipeline run failed). Refuse instead of writing a misleading file.
        raise SystemExit(
            f"Only {len(facts_per_annotator)} annotator(s) for doc {doc_id!r} under "
            f"{facts_root} — need >= 2 for a meaningful conflict analysis. "
            f"(Did an extraction cell fail?)")

    print(f"[phase2] annotators: {list(facts_per_annotator.keys())}")
    print(f"[phase2] fact counts: { {a: len(f) for a,f in facts_per_annotator.items()} }")

    pairs = align_all_pairs(facts_per_annotator, threshold=align_threshold)
    print(f"[phase2] aligned pairs: {len(pairs)}")

    l1_counts = classify_all(pairs, Layer1Config(redundancy_cosine=redundancy_cosine))
    print(f"[phase2] Layer-1 counts: {l1_counts}")

    if skip_layer2:
        final = arbitrate_all(pairs, chat_fn=_stub_layer2, doc_title=doc_id)
        print(f"[phase2] Layer-2 (STUB) counts: { {k:v for k,v in final.items() if not k.startswith('_')} }")
    else:
        title = doc_id
        parsed_path = parsed_root / f"{doc_id}.json"
        if parsed_path.exists():
            parsed = json.loads(parsed_path.read_text(encoding="utf-8"))
            title = parsed.get("document", {}).get("title", doc_id)
        final = arbitrate_all(pairs, model_name=layer2_model, doc_title=title, base_url=layer2_url)
        print(f"[phase2] Layer-2 ({layer2_model}) counts: { {k:v for k,v in final.items() if not k.startswith('_')} }")

    surface_to_cluster = cluster_entities(facts_per_annotator,
                                          merge_threshold=merge_threshold,
                                          core_entities=True)
    n_clusters = len(set(surface_to_cluster.values()))
    print(f"[phase2] entity clusters: {n_clusters} (from {len(surface_to_cluster)} surface forms)")

    out_root.mkdir(parents=True, exist_ok=True)
    out_path = out_root / (f"{doc_id}__{out_suffix}.json"
                           if out_suffix else f"{doc_id}.json")
    payload = {
        "doc_id": doc_id,
        "params": {
            "align_threshold": align_threshold,
            "redundancy_cosine": redundancy_cosine,
            "merge_threshold": merge_threshold,
            "layer2_model": layer2_model if not skip_layer2 else "stub",
            "core_entities": True,
            "facts_root": str(facts_root),
            "variant": out_suffix or None,
        },
        "annotators": list(facts_per_annotator.keys()),
        "facts_per_annotator": facts_per_annotator,
        "aligned_pairs": [pair_to_dict(p) for p in pairs],
        "entity_clusters": surface_to_cluster,
        "label_counts": {k: v for k, v in final.items() if not k.startswith("_")},
        "layer2_calls": final.get("_layer2_calls", 0),
    }
    out_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[phase2] wrote {out_path}")
    return out_path


def main():
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--doc", default=None,
                   help="A single doc_id. Mutually exclusive with --all.")
    p.add_argument("--all", action="store_true",
                   help="Run every doc_id that has >=2 annotators under --facts-root.")
    p.add_argument("--facts-root", type=Path, default=Path("data/facts"))
    p.add_argument("--parsed-root", type=Path, default=Path("data/parsed"))
    p.add_argument("--out-root", type=Path, default=Path("data/conflicts"))
    p.add_argument("--align-threshold", type=float, default=0.55)
    p.add_argument("--redundancy-cosine", type=float, default=0.90)
    p.add_argument("--merge-threshold", type=float, default=0.78)
    p.add_argument("--layer2-model", default="qwen3.5:4b")
    p.add_argument("--layer2-url", default="http://localhost:11434")
    p.add_argument("--skip-layer2", action="store_true",
                   help="Use deterministic stub instead of calling Ollama.")
    p.add_argument("--fail-fast", action="store_true",
                   help="Abort the batch on first doc failure (default: continue).")
    args = p.parse_args()

    if args.all and args.doc:
        p.error("--all and --doc are mutually exclusive")

    if args.all:
        doc_ids = discover_all_doc_ids(args.facts_root)
        if not doc_ids:
            p.error(f"No docs with >=2 annotators found under {args.facts_root}")
        preview = doc_ids[:5]
        suffix = "..." if len(doc_ids) > 5 else ""
        print(f"[phase2-all] discovered {len(doc_ids)} doc(s): {preview}{suffix}")
    else:
        doc_ids = [args.doc or "train-000000"]

    agg_counts: dict[str, int] = {}
    n_ok = 0
    n_err = 0
    for i, doc_id in enumerate(doc_ids, 1):
        if len(doc_ids) > 1:
            print(f"\n=== [{i}/{len(doc_ids)}] {doc_id} ===")
        try:
            out_path = run(
                doc_id,
                facts_root=args.facts_root,
                parsed_root=args.parsed_root,
                out_root=args.out_root,
                align_threshold=args.align_threshold,
                redundancy_cosine=args.redundancy_cosine,
                merge_threshold=args.merge_threshold,
                layer2_model=args.layer2_model,
                skip_layer2=args.skip_layer2,
                layer2_url=args.layer2_url,
            )
            confl = json.loads(out_path.read_text(encoding="utf-8"))
            for k, v in confl.get("label_counts", {}).items():
                agg_counts[k] = agg_counts.get(k, 0) + v
            n_ok += 1
        except SystemExit:
            raise
        except Exception as e:
            n_err += 1
            print(f"[phase2-all] FAILED on {doc_id}: {type(e).__name__}: {e}")
            if args.fail_fast:
                raise

    if len(doc_ids) > 1:
        print(f"\n[phase2-all] done. ok={n_ok}  err={n_err}")
        print(f"[phase2-all] aggregate label counts: {agg_counts}")


if __name__ == "__main__":
    main()
