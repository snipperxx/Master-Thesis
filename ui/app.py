"""
Flask backend for the Visual Analytics prototype.

All endpoints:
    GET  /                              single-page UI
    GET  /api/docs                      docs on disk (with phase-2 flag)
    GET  /api/doc/<id>                  parsed text + section offsets
    GET  /api/facts/<id>                facts with filters
    GET  /api/pairs/<id>                aligned-pair table
    GET  /api/graph/<id>                Cytoscape elements; ?merge_threshold= recomputes
    GET  /api/coverage/<id>             per-annotator coverage metrics
    GET  /api/distribution_shift/<id>   v1 vs v2 conflict-label diff
    POST /api/verify/<doc>/<fact_id>    persist verified/rejected
    POST /api/upload_text               ingest user-pasted text -> ParsedDocument
    GET  /api/guidelines                list available prompts/extract_*.md
    GET  /api/guidelines/<version>      get one
    PUT  /api/guidelines/<version>      save (creates new version file)
    POST /api/reextract_span            re-extract a section/span with chosen model+guideline

    Background jobs:
    POST /api/guideline                 enqueue Phase-4 re-extraction job (new guideline body)
    POST /api/run_phase1                enqueue Phase-1 batch (existing guideline_version)
    POST /api/run_phase2                enqueue Phase-2 batch (alignment + conflict detect)
    POST /api/run_matrix                multi-cell scheduling
    GET  /api/run_matrix/status         existing fact counts per (doc, model)
    GET  /api/jobs[/<job_id>]           list + poll jobs (all kinds)
    POST /api/jobs/<job_id>/cancel      cancel a queued job
"""

from __future__ import annotations
import os

import json
import logging
import re
from pathlib import Path
from typing import Any

from flask import Flask, abort, jsonify, render_template, request

REPO_ROOT = Path(__file__).resolve().parent.parent
DATA = REPO_ROOT / "data"
PARSED = DATA / "parsed"
FACTS = DATA / "facts"
CONFLICTS = DATA / "conflicts"
GRAPHS = DATA / "graphs"
VERIFICATIONS = DATA / "verifications"
PROMPTS = REPO_ROOT / "prompts"

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("ui")

app = Flask(__name__,
            template_folder=str(REPO_ROOT / "ui" / "templates"),
            static_folder=str(REPO_ROOT / "ui" / "static"))
# Dev ergonomics on this machine: the Jinja template cache missed on-disk
# changes (mount mtime semantics + non-debug runs), serving stale HTML while
# static JS was already fresh. Force per-request template mtime checks and
# disable static-file caching regardless of --debug.
app.config["TEMPLATES_AUTO_RELOAD"] = True
app.config["SEND_FILE_MAX_AGE_DEFAULT"] = 0
app.jinja_env.auto_reload = True


@app.errorhandler(Exception)
def _json_errors_for_api(exc):
    """Return JSON (not Flask's HTML page) for any /api/* path failure.

    Prevents the front-end's `response.json()` from blowing up with
    'Unexpected token <' when the backend hits an abort() or unhandled
    exception. Non-/api paths fall back to Flask's default handler.
    """
    from werkzeug.exceptions import HTTPException
    if not request.path.startswith("/api/"):
        # Let Flask render its normal HTML error page for non-API routes.
        raise exc
    if isinstance(exc, HTTPException):
        return jsonify({"error": exc.name, "status": exc.code,
                        "message": exc.description or str(exc)}), exc.code
    log.exception("Unhandled /api exception on %s", request.path)
    return jsonify({"error": type(exc).__name__, "status": 500,
                    "message": str(exc)}), 500


def _load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _safe_model_dir(name: str) -> str:
    return name.replace(":", "_").replace("/", "_").replace(".", "_")


def _doc_ids_available() -> list[str]:
    return sorted(p.stem for p in PARSED.glob("*.json")) if PARSED.exists() else []


def _docs_with_conflicts() -> set[str]:
    if not CONFLICTS.exists():
        return set()
    return {p.stem for p in CONFLICTS.glob("*.json") if "__" not in p.stem}


def _variant_arg() -> str:
    """?variant=<guideline_version> selects data/conflicts/<doc>__<v>.json.

    Empty / "v1" / "base" mean the baseline file. Validation mirrors
    _GUIDELINE_VERSION_RE (defined below; resolved at request time)."""
    v = (request.args.get("variant") or "").strip()
    if v in ("", "v1", "base"):
        return ""
    if not _GUIDELINE_VERSION_RE.match(v):
        abort(400, "invalid variant")
    return v


def _conflicts_path(doc_id: str) -> Path:
    v = _variant_arg()
    return CONFLICTS / (f"{doc_id}__{v}.json" if v else f"{doc_id}.json")


def _graph_cache_path(doc_id: str) -> Path:
    v = _variant_arg()
    return GRAPHS / (f"{doc_id}__{v}.json" if v else f"{doc_id}.json")


def _doc_variants(doc_id: str) -> list[str]:
    if not CONFLICTS.exists():
        return []
    return sorted(p.stem.split("__", 1)[1] for p in CONFLICTS.glob(f"{doc_id}__*.json"))


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/api/docs")
def api_docs():
    out = []
    with_confl = _docs_with_conflicts()
    for doc_id in _doc_ids_available():
        ppath = PARSED / f"{doc_id}.json"
        try:
            parsed = _load_json(ppath)["document"]
        except Exception:
            continue
        entry = {
            "doc_id": doc_id, "title": parsed.get("title", doc_id),
            "has_conflicts": doc_id in with_confl,
            "is_user_doc": doc_id.startswith("user-"),
        }
        entry["variants"] = _doc_variants(doc_id)
        if entry["has_conflicts"]:
            try:
                confl = _load_json(CONFLICTS / f"{doc_id}.json")
                entry["annotators"] = confl.get("annotators", [])
                entry["label_counts"] = confl.get("label_counts", {})
            except Exception:
                pass
        out.append(entry)
    return jsonify({"docs": out})


@app.route("/api/doc/<doc_id>", methods=["GET", "DELETE"])
def api_doc(doc_id: str):
    path = PARSED / f"{doc_id}.json"
    if request.method == "DELETE":
        # Remove every per-doc artefact across all data subdirs.
        removed: list[str] = []
        # parsed
        if path.exists():
            path.unlink(); removed.append(str(path.relative_to(REPO_ROOT)))
        # facts (one file per annotator)
        if FACTS.exists():
            for sub in FACTS.iterdir():
                if not sub.is_dir():
                    continue
                fp = sub / f"{doc_id}.json"
                if fp.exists():
                    fp.unlink(); removed.append(str(fp.relative_to(REPO_ROOT)))
        # conflicts (main + __v2_* variants)
        if CONFLICTS.exists():
            for fp in [CONFLICTS / f"{doc_id}.json", *CONFLICTS.glob(f"{doc_id}__*.json")]:
                if fp.exists():
                    fp.unlink(); removed.append(str(fp.relative_to(REPO_ROOT)))
        # graphs (incl. variants) + verifications + reviews + versioned facts roots
        for root in (GRAPHS, VERIFICATIONS, DATA / "reviews"):
            for fp in [root / f"{doc_id}.json", *root.glob(f"{doc_id}__*.json")] if root.exists() else []:
                if fp.exists():
                    fp.unlink(); removed.append(str(fp.relative_to(REPO_ROOT)))
        for froot in DATA.glob("facts__*"):
            for sub in froot.iterdir():
                if sub.is_dir():
                    fp = sub / f"{doc_id}.json"
                    if fp.exists():
                        fp.unlink(); removed.append(str(fp.relative_to(REPO_ROOT)))
        if not removed:
            abort(404, f"doc {doc_id!r} has no files on disk")
        return jsonify({"ok": True, "doc_id": doc_id,
                        "removed": removed, "n_removed": len(removed)})
    # GET
    if not path.exists():
        abort(404, f"parsed doc {doc_id!r} not found")
    parsed = _load_json(path)["document"]
    return jsonify({
        "doc_id": doc_id, "title": parsed.get("title"),
        "citations": parsed.get("citations", []),
        "preamble_text": parsed.get("preamble_text", ""),
        "enacting_text": parsed.get("enacting_text", ""),
        "recitals": parsed.get("recitals", []),
        "articles": parsed.get("articles", []),
        "concluding_formulas": parsed.get("concluding_formulas", ""),
    })


@app.route("/api/facts/<doc_id>")
def api_facts(doc_id: str):
    path = _conflicts_path(doc_id)
    if not path.exists():
        return _facts_from_disk_only(doc_id)
    confl = _load_json(path)
    annotators = request.args.getlist("annotator")
    conflict = request.args.get("conflict")

    # Always resolve per-fact edge labels (not only when filtering) so the
    # top-bar label counts and the facts-table Conflict column are truthful.
    edge_label_by_fact: dict[str, str] = {}
    if True:
        gp = _graph_cache_path(doc_id)
        if gp.exists():
            for edge in _load_json(gp).get("edges", []):
                label = edge["data"]["conflict_label"]
                for fid in edge["data"]["fact_ids"]:
                    cur = edge_label_by_fact.get(fid)
                    if cur is None or label > cur:
                        edge_label_by_fact[fid] = label

    vpath = VERIFICATIONS / f"{doc_id}.json"
    verifications = _load_json(vpath) if vpath.exists() else {}

    out: list[dict] = []
    for annot, facts in confl["facts_per_annotator"].items():
        if annotators and annot not in annotators:
            continue
        for f in facts:
            f2 = dict(f)
            f2["_annotator"] = annot
            f2["_edge_conflict_label"] = edge_label_by_fact.get(f["fact_id"], "unlabeled")
            f2["_verification"] = verifications.get(f["fact_id"])
            if conflict and f2["_edge_conflict_label"] != conflict:
                continue
            out.append(f2)
    return jsonify({"doc_id": doc_id, "facts": out, "count": len(out)})


def _facts_from_disk_only(doc_id: str):
    if not PARSED.joinpath(f"{doc_id}.json").exists():
        abort(404, f"doc {doc_id!r} not found")
    out: list[dict] = []
    for sub in sorted(FACTS.iterdir()) if FACTS.exists() else []:
        if not sub.is_dir():
            continue
        fp = sub / f"{doc_id}.json"
        if not fp.exists():
            continue
        try:
            doc = json.loads(fp.read_text(encoding="utf-8"))
        except Exception:
            continue
        for f in doc.get("facts", []):
            f2 = dict(f)
            f2["_annotator"] = doc.get("annotator") or sub.name
            f2["_edge_conflict_label"] = "unlabeled"
            f2["_verification"] = None
            out.append(f2)
    return jsonify({"doc_id": doc_id, "facts": out, "count": len(out)})


@app.route("/api/pairs/<doc_id>")
def api_pairs(doc_id: str):
    path = _conflicts_path(doc_id)
    return jsonify(_load_json(path)["aligned_pairs"]) if path.exists() else jsonify([])


@app.route("/api/graph/<doc_id>")
def api_graph(doc_id: str):
    """Cytoscape graph. Built from the conflicts doc when conflict detection has
    run (edges carry conflict colours); otherwise built from facts alone — entity
    clustering + S-P-O edges, all `unlabeled` — so the KG is viewable before (or
    without) Phase-2. Conflict labels are an overlay added once Phase-2 runs."""
    from scripts.run_phase2 import cluster_entities, discover_annotators
    from src.kg_build import build_graph, build_reified_graph
    from src import reextract_worker as rw

    cpath = _conflicts_path(doc_id)
    labeled = cpath.exists()
    if labeled:
        confl = _load_json(cpath)
    else:
        # No conflict detection yet — assemble a facts-only doc from disk.
        version = _variant_arg()
        facts_root = rw.facts_root_for_version(version or "v1")
        fpa = discover_annotators(facts_root, doc_id)
        if not fpa:
            return jsonify({"doc_id": doc_id, "nodes": [], "edges": [],
                            "summary": {"n_nodes": 0, "n_edges": 0, "edge_label_counts": {}}})
        confl = {"doc_id": doc_id, "params": {}, "annotators": list(fpa),
                 "facts_per_annotator": fpa, "aligned_pairs": [],
                 "entity_clusters": {}, "label_counts": {}}

    params = confl.get("params", {})
    want_thr = float(request.args.get("merge_threshold",
                                      params.get("merge_threshold", 0.78)))
    core_flag = request.args.get("core", "1") != "0"
    # reify=1 -> statement-node graph (mirrors the Neo4j reification); cached in
    # a SEPARATE file so the flat graph (still used by the Multiples tab) and the
    # reified graph for the same doc never overwrite each other.
    reify = request.args.get("reify", "0") != "0"
    gpath = _graph_cache_path(doc_id)
    if reify:
        gpath = gpath.with_name(gpath.stem + "__reified.json")

    # Serve cache only when knobs match AND the labeled/unlabeled mode matches
    # (so a Phase-2 run's labelled graph supersedes an earlier facts-only one).
    if gpath.exists():
        g = _load_json(gpath)
        gp = g.get("params", {})
        try:
            if (abs(float(gp.get("merge_threshold", -1)) - want_thr) < 1e-9
                    and bool(gp.get("core_entities", False)) == core_flag
                    and bool(gp.get("labeled", True)) == labeled
                    and bool(g.get("reified", False)) == reify):
                return jsonify(g)
        except (TypeError, ValueError):
            pass

    cfb = dict(confl)
    cfb["entity_clusters"] = cluster_entities(
        confl["facts_per_annotator"], merge_threshold=want_thr,
        core_entities=core_flag)
    cfb["params"] = dict(params)
    cfb["params"]["merge_threshold"] = want_thr
    cfb["params"]["core_entities"] = core_flag
    graph = build_reified_graph(cfb) if reify else build_graph(cfb)
    graph.setdefault("params", {})["labeled"] = labeled
    gpath.parent.mkdir(parents=True, exist_ok=True)
    gpath.write_text(json.dumps(graph, ensure_ascii=False, indent=2), encoding="utf-8")
    return jsonify(graph)


@app.route("/api/verify/<doc_id>/<fact_id>", methods=["POST"])
def api_verify(doc_id: str, fact_id: str):
    payload = request.get_json(silent=True) or {}
    status = payload.get("status")
    if status not in ("verified", "rejected", "unset"):
        abort(400, "status must be one of: verified, rejected, unset")
    VERIFICATIONS.mkdir(parents=True, exist_ok=True)
    vpath = VERIFICATIONS / f"{doc_id}.json"
    cur = _load_json(vpath) if vpath.exists() else {}
    if status == "unset":
        cur.pop(fact_id, None)
    else:
        cur[fact_id] = {"status": status, "note": payload.get("note", "")}
    vpath.write_text(json.dumps(cur, ensure_ascii=False, indent=2), encoding="utf-8")
    return jsonify({"ok": True, "fact_id": fact_id, "status": status, "n_verified": len(cur)})


@app.route("/api/coverage/<doc_id>")
def api_coverage(doc_id: str):
    cpath, ppath = _conflicts_path(doc_id), PARSED / f"{doc_id}.json"
    if not cpath.exists() or not ppath.exists():
        return jsonify({})
    from src.coverage import compute_coverage
    return jsonify(compute_coverage(_load_json(cpath)["facts_per_annotator"], _load_json(ppath)))


@app.route("/api/distribution_shift/<doc_id>")
def api_distribution_shift(doc_id: str):
    from src.distribution_shift import compare_conflict_files
    v1_label = request.args.get("v1", "v1")
    v2_label = request.args.get("v2")
    def _path(version: str) -> Path:
        return CONFLICTS / (f"{doc_id}.json" if version == "v1" else f"{doc_id}__{version}.json")
    def _empty(note: str) -> dict:
        return {"v1_label": v1_label, "v2_label": v2_label,
                "note": note,
                "labels": [], "v1": [], "v2": [], "delta": [], "delta_pct": [],
                "totals": {"v1": 0, "v2": 0, "delta": 0, "delta_pct": None},
                "layer1_rate": {"v1": {}, "v2": {}}}
    p1 = _path(v1_label)
    if not p1.exists():
        return jsonify(_empty(
            f"No Phase-2 output for '{v1_label}' on this doc yet — "
            f"run Phase-2 first (Background runs tab)."))
    if v2_label is None:
        cands = sorted(CONFLICTS.glob(f"{doc_id}__*.json"), key=lambda p: p.stat().st_mtime)
        if not cands:
            return jsonify(_empty(
                "No v2 conflicts file found yet — "
                "save a new guideline version then re-run Phase-1 + Phase-2."))
        p2 = cands[-1]
        v2_label = p2.stem.split("__", 1)[1]
    else:
        p2 = _path(v2_label)
        if not p2.exists():
            return jsonify(_empty(f"conflicts for '{v2_label}' not found on disk"))
    try:
        out = compare_conflict_files(p1, p2, v1_label=v1_label, v2_label=v2_label)
        # Annotator sets of both sides: when they differ, pair counts are not
        # comparable (fewer annotator pairs => mechanically fewer aligned
        # pairs) and the UI must say so before anyone reads the deltas.
        try:
            out["v1_annotators"] = sorted(_load_json(p1).get("annotators", []))
            out["v2_annotators"] = sorted(_load_json(p2).get("annotators", []))
            out["annotators_match"] = out["v1_annotators"] == out["v2_annotators"]
        except Exception:
            pass
        return jsonify(out)
    except Exception as exc:
        log.exception("distribution_shift failed for %s", doc_id)
        return jsonify(_empty(f"comparison failed: {type(exc).__name__}: {exc}"))


# ===================== uploads & guidelines ============================


@app.route("/api/upload_text", methods=["POST"])
def api_upload_text():
    payload = request.get_json(silent=True) or {}
    title = (payload.get("title") or "").strip()
    text = (payload.get("text") or "")
    split_mode = payload.get("split_mode", "paragraph")
    if not text.strip():
        abort(400, "text is required")

    from src.text_ingest import build_parsed_doc, write_parsed_doc
    parsed = build_parsed_doc(title=title, text=text, split_mode=split_mode)
    out = write_parsed_doc(PARSED, parsed)
    doc_id = parsed["document"]["celex"]
    response: dict = {"ok": True, "doc_id": doc_id,
                      "title": parsed["document"]["title"],
                      "n_sections": len(parsed["document"]["recitals"]),
                      "parsed_path": str(out)}

    if payload.get("extract"):
        from src import reextract_worker as rw
        model = payload.get("model") or "qwen3.5:4b"
        guideline_version = payload.get("guideline_version") or "v1"
        existing = PROMPTS / f"extract_{guideline_version}.md"
        if not existing.exists():
            abort(400, f"guideline {guideline_version!r} not found")
        job = rw.enqueue_reextract_with_version(
            guideline_version, models=[model], doc_paths=[response["parsed_path"]],
            label=f"upload extract ({model} × {doc_id})",
        )
        response["job_id"] = job.job_id
    return jsonify(response), 201


_GUIDELINE_VERSION_RE = re.compile(r"^[A-Za-z0-9_.\-]+$")

def _guideline_path(version: str) -> Path:
    if not _GUIDELINE_VERSION_RE.match(version or ""):
        abort(400, "version must be alphanumeric/underscore/dot/dash")
    return PROMPTS / f"extract_{version}.md"


@app.route("/api/arbitrate_prompts")
def api_arbitrate_prompts_list():
    """List all prompts/arbitrate_*.md versions."""
    out = []
    for p in sorted(PROMPTS.glob("arbitrate_*.md")):
        version = p.stem.removeprefix("arbitrate_")
        out.append({"version": version, "filename": p.name, "size": p.stat().st_size})
    return jsonify({"arbitrate_prompts": out})


@app.route("/api/schema_prompts")
def api_schema_prompts_list():
    """List all prompts/schema_*.md versions."""
    out = []
    for p in sorted(PROMPTS.glob("schema_*.md")):
        version = p.stem.removeprefix("schema_")
        out.append({"version": version, "filename": p.name, "size": p.stat().st_size})
    return jsonify({"schema_prompts": out})


@app.route("/api/schema_prompt", methods=["GET", "PUT", "DELETE"])
def api_schema_prompt():
    """Read/write/delete prompts/schema_<version>.md."""
    version = (request.args.get("version") or "v1").strip()
    if not _GUIDELINE_VERSION_RE.match(version):
        abort(400, "version must be alphanumeric/underscore/dot/dash")
    path = PROMPTS / f"schema_{version}.md"
    if request.method == "DELETE":
        if version == "v1":
            abort(403, "v1 schema is protected and cannot be deleted")
        if not path.exists():
            abort(404, f"schema {version!r} not found")
        path.unlink()
        return jsonify({"ok": True, "version": version, "deleted": True})
    if request.method == "GET":
        if not path.exists():
            abort(404, f"schema {version!r} not found")
        return jsonify({"version": version, "text": path.read_text(encoding="utf-8"),
                        "filename": path.name})
    payload = request.get_json(silent=True) or {}
    text = payload.get("text")
    if not isinstance(text, str) or not text.strip():
        abort(400, "text required")
    path.write_text(text, encoding="utf-8")
    return jsonify({"version": version, "saved": True, "size": path.stat().st_size})


@app.route("/api/arbitrate_prompt", methods=["GET", "PUT", "DELETE"])
def api_arbitrate_prompt():
    """Read/write/delete prompts/arbitrate_<version>.md (the conflict-detection prompt)."""
    version = (request.args.get("version") or "v1").strip()
    if not _GUIDELINE_VERSION_RE.match(version):
        abort(400, "version must be alphanumeric/underscore/dot/dash")
    path = PROMPTS / f"arbitrate_{version}.md"
    if request.method == "DELETE":
        if version == "v1":
            abort(403, "v1 is the baseline arbitrate prompt and cannot be deleted")
        if not path.exists():
            abort(404, f"arbitrate prompt {version!r} not found")
        path.unlink()
        return jsonify({"version": version, "deleted": True})
    if request.method == "GET":
        if not path.exists():
            abort(404, f"arbitrate prompt {version!r} not found")
        return jsonify({"version": version, "text": path.read_text(encoding="utf-8"),
                        "filename": path.name})
    payload = request.get_json(silent=True) or {}
    text = payload.get("text")
    if not isinstance(text, str) or not text.strip():
        abort(400, "text required")
    path.write_text(text, encoding="utf-8")
    return jsonify({"version": version, "saved": True, "size": path.stat().st_size})


@app.route("/api/arbitrate_test", methods=["POST"])
def api_arbitrate_test():
    """Run one conflict-detection (Layer-2) arbitration on a pair and return the
    raw label/reason. Lenient — accepts any label the prompt defines."""
    payload = request.get_json(silent=True) or {}
    from src.conflict_layer2 import arbitrate_once
    try:
        res = arbitrate_once(
            doc_title=payload.get("doc_title", ""),
            source_quote=payload.get("source_quote", ""),
            fact_a=payload.get("fact_a", ""),
            fact_b=payload.get("fact_b", ""),
            model_name=payload.get("model") or "qwen3.5:4b",
            base_url=os.environ.get("OLLAMA_URL", "http://localhost:11434"),
            template=payload.get("prompt"),
            version=payload.get("version", "v1"),
        )
    except Exception as exc:
        return jsonify({"error": f"{type(exc).__name__}: {exc}"}), 200
    return jsonify(res)


@app.route("/api/guidelines")
def api_guidelines_list():
    if not PROMPTS.exists():
        return jsonify({"guidelines": []})
    out = []
    for p in sorted(PROMPTS.glob("extract_*.md")):
        ver = p.stem.removeprefix("extract_")
        try:
            body = p.read_text(encoding="utf-8")
            m = re.search(r"#+\s*Guideline\s*\(([^)]+)\)", body)
            summary = m.group(0) if m else body.strip().split("\n", 1)[0][:120]
            out.append({"version": ver, "filename": p.name,
                        "size": p.stat().st_size, "mtime": p.stat().st_mtime,
                        "summary": summary})
        except Exception as exc:
            log.warning("guideline %s unreadable: %s", p, exc)
    return jsonify({"guidelines": out})


@app.route("/api/guidelines/<version>", methods=["GET", "PUT", "DELETE"])
def api_guideline_one(version: str):
    path = _guideline_path(version)
    if request.method == "DELETE":
        if version == "v1":
            abort(403, "v1 is the baseline guideline and cannot be deleted")
        if not path.exists():
            abort(404, f"guideline {version!r} not found")
        path.unlink()
        return jsonify({"ok": True, "version": version, "deleted": True})
    if request.method == "GET":
        if not path.exists():
            abort(404, f"guideline {version!r} not found")
        return jsonify({"version": version, "text": path.read_text(encoding="utf-8"),
                        "filename": path.name, "size": path.stat().st_size})
    payload = request.get_json(silent=True) or {}
    text = payload.get("text", "")
    if not text.strip():
        abort(400, "text body is required")
    required = ("<<DOC_TITLE>>", "<<SECTION_PATH>>", "<<SECTION_TEXT>>")
    missing = [s for s in required if s not in text]
    if missing:
        abort(400, f"guideline must contain sentinels: {missing}")
    PROMPTS.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return jsonify({"ok": True, "version": version, "size": path.stat().st_size}), 200


@app.route("/api/reextract_span", methods=["POST"])
def api_reextract_span():
    payload = request.get_json(silent=True) or {}
    doc_id = payload.get("doc_id")
    model = payload.get("model") or "qwen3.5:4b"
    guideline_version = payload.get("guideline_version", "v1")
    section_path = payload.get("section_path")
    char_start = payload.get("char_start")
    char_end = payload.get("char_end")
    if not doc_id:
        abort(400, "doc_id is required")

    ppath = PARSED / f"{doc_id}.json"
    if not ppath.exists():
        abort(404, f"parsed doc {doc_id!r} not found")
    parsed = _load_json(ppath)["document"]

    if section_path:
        from src.extractor import enumerate_sections
        from src.schema import ParsedDocument
        doc_pyd = ParsedDocument(**parsed)
        target = next((s for s in enumerate_sections(doc_pyd) if s.section_path == section_path), None)
        if target is None:
            abort(404, f"section_path {section_path!r} not found in doc")
        section_text = target.text
        eff_start, eff_end = target.base_offset, target.base_offset + len(target.text)
    elif char_start is not None and char_end is not None:
        cs, ce = int(char_start), int(char_end)
        if cs < 0 or ce <= cs:
            abort(400, "invalid char_start/char_end")
        section_text = parsed["preamble_text"][cs:ce]
        if not section_text.strip():
            abort(400, "selected span is empty")
        section_path = f"preamble.span[{cs}:{ce}]"
        eff_start, eff_end = cs, ce
    else:
        abort(400, "either section_path or (char_start,char_end) is required")

    mini = {
        "celex": doc_id, "title": parsed.get("title", doc_id),
        "citations": [], "annexes": [], "articles": [], "concluding_formulas": "",
        "preamble_text": parsed.get("preamble_text", ""),
        "enacting_text": parsed.get("enacting_text", ""),
        "recitals": [{"number": "(span)", "text": section_text,
                      "char_start": eff_start, "char_end": eff_end}],
    }
    from src.schema import ParsedDocument
    from src.extractor import extract, write_facts
    mini_doc = ParsedDocument(**mini)

    try:
        new_facts = extract(model_name=model, parsed_doc=mini_doc,
                            guideline_version=guideline_version,
                            ensure_solo=True, unload_after=False)
    except Exception as exc:
        log.exception("reextract_span failed")
        abort(500, f"{type(exc).__name__}: {exc}")

    fp = FACTS / _safe_model_dir(model) / f"{doc_id}.json"
    if fp.exists():
        existing = json.loads(fp.read_text(encoding="utf-8"))
        kept = []
        for f in existing.get("facts", []):
            loc = f.get("source_locator", {})
            if loc.get("section_path", "").startswith("preamble") and \
               loc.get("char_start") is not None and \
               eff_start <= loc.get("char_start", -1) < eff_end:
                continue
            kept.append(f)
        existing["facts"] = kept + [fact.model_dump(mode="json") for fact in new_facts]
        existing["fact_count"] = len(existing["facts"])
        fp.write_text(json.dumps(existing, ensure_ascii=False, indent=2), encoding="utf-8")
    else:
        write_facts(new_facts, model_name=model, doc_id=doc_id,
                    guideline_version=guideline_version, out_root=FACTS)

    return jsonify({"ok": True, "doc_id": doc_id, "model": model,
                    "guideline_version": guideline_version,
                    "section_path": section_path,
                    "char_start": eff_start, "char_end": eff_end,
                    "n_new_facts": len(new_facts)})


# ===================== Background jobs ============================


@app.route("/api/guideline", methods=["POST"])
def api_guideline_save_and_enqueue():
    payload = request.get_json(silent=True) or {}
    guideline_text = payload.get("guideline_text", "")
    if not guideline_text.strip():
        abort(400, "guideline_text is required")
    models = payload.get("models") or ["qwen3.5:4b"]
    doc_paths = payload.get("doc_paths")
    if not doc_paths:
        doc_paths = [str(p) for p in sorted(PARSED.glob("*.json"))]
    from src import reextract_worker as rw
    job = rw.enqueue(guideline_text, models=models, doc_paths=doc_paths,
                     label=f"new-guideline re-extract ({len(models)}m × {len(doc_paths)}d)")
    return jsonify(rw.to_dict(job)), 202


@app.route("/api/run_phase1", methods=["POST"])
def api_run_phase1():
    """Phase-1 batch from UI: extract on all (model × doc) cells with existing guideline."""
    payload = request.get_json(silent=True) or {}
    models = payload.get("models") or ["qwen3.5:4b"]
    guideline_version = payload.get("guideline_version", "v1")
    doc_ids = payload.get("doc_ids")
    if doc_ids:
        doc_paths = [str(PARSED / f"{d}.json")
                     for d in doc_ids if (PARSED / f"{d}.json").exists()]
    else:
        doc_paths = [str(p) for p in sorted(PARSED.glob("*.json"))]
    if not doc_paths:
        abort(400, "no parsed docs available")
    from src import reextract_worker as rw
    try:
        job = rw.enqueue_reextract_with_version(
            guideline_version, models=models, doc_paths=doc_paths,
            label=f"phase-1 ({guideline_version}, {len(models)}m × {len(doc_paths)}d)")
    except FileNotFoundError as exc:
        abort(400, str(exc))
    return jsonify(rw.to_dict(job)), 202


@app.route("/api/run_phase2", methods=["POST"])
def api_run_phase2():
    """Phase-2 batch from UI: alignment + Layer-1 + Layer-2 over N docs."""
    from scripts.run_phase2 import discover_all_doc_ids
    payload = request.get_json(silent=True) or {}
    from src import reextract_worker as rw
    version = (payload.get("version") or "v1").strip()
    facts_root = rw.facts_root_for_version(version)
    out_suffix = "" if version in ("", "v1") else version
    doc_ids = payload.get("doc_ids")
    if not doc_ids:
        doc_ids = discover_all_doc_ids(facts_root)
    if not doc_ids:
        abort(400, f"no docs with >=2 annotators found under {facts_root}")
    arbitrate_version = (payload.get("arbitrate_version") or "v1").strip()
    if not _GUIDELINE_VERSION_RE.match(arbitrate_version):
        arbitrate_version = "v1"
    params = {
        "skip_layer2": bool(payload.get("skip_layer2", True)),
        "align_threshold": float(payload.get("align_threshold", 0.78)),
        "redundancy_cosine": float(payload.get("redundancy_cosine", 0.95)),
        "merge_threshold": float(payload.get("merge_threshold", 0.78)),
        "layer2_model": payload.get("layer2_model", "qwen3.5:4b"),
        "layer2_url": payload.get("layer2_url", os.environ.get("OLLAMA_URL", "http://localhost:11434")),
        "facts_root": str(facts_root),
        "out_suffix": out_suffix,
        "arbitrate_version": arbitrate_version,
    }
    job = rw.enqueue_phase2(
        doc_ids=doc_ids, params=params,
        label=f"phase-2 ({version}, {len(doc_ids)} docs, {'stub' if params['skip_layer2'] else params['layer2_model']}, arb={arbitrate_version})")
    return jsonify(rw.to_dict(job)), 202


@app.route("/api/run_matrix", methods=["POST"])
def api_run_matrix():
    payload = request.get_json(silent=True) or {}
    cells = payload.get("cells", [])
    if not isinstance(cells, list) or not cells:
        abort(400, "cells must be a non-empty list")
    from src import reextract_worker as rw
    out: list[dict] = []
    for cell in cells:
        doc_id = cell.get("doc_id"); model = cell.get("model")
        version = cell.get("guideline_version", "v1")
        if not (doc_id and model):
            out.append({"cell": cell, "error": "missing doc_id or model"}); continue
        ppath = PARSED / f"{doc_id}.json"
        if not ppath.exists():
            out.append({"cell": cell, "error": f"doc {doc_id} not found"}); continue
        gpath = PROMPTS / f"extract_{version}.md"
        if not gpath.exists():
            out.append({"cell": cell, "error": f"guideline {version} not found"}); continue
        try:
            job = rw.enqueue_reextract_with_version(
                version, models=[model], doc_paths=[str(ppath)],
                label=f"matrix cell ({model} × {doc_id} × {version})")
            out.append({"cell": cell, "job_id": job.job_id})
        except Exception as exc:
            out.append({"cell": cell, "error": f"{type(exc).__name__}: {exc}"})
    return jsonify({"jobs": out}), 202


@app.route("/api/run_matrix/status")
def api_run_matrix_status():
    out: dict[str, dict] = {}
    for sub in sorted(FACTS.iterdir()) if FACTS.exists() else []:
        if not sub.is_dir(): continue
        for fp in sub.glob("*.json"):
            try: d = json.loads(fp.read_text(encoding="utf-8"))
            except Exception: continue
            key = f"{d.get('doc_id', fp.stem)}|{d.get('annotator', sub.name)}"
            out[key] = {"doc_id": d.get("doc_id", fp.stem),
                        "model": d.get("annotator", sub.name),
                        "guideline_version": d.get("guideline_version", "v1"),
                        "fact_count": int(d.get("fact_count", 0))}
    return jsonify(out)


@app.route("/api/jobs/<job_id>")
def api_job_status(job_id: str):
    from src import reextract_worker as rw
    job = rw.get_job(job_id)
    if job is None:
        abort(404, f"job {job_id!r} not found")
    return jsonify(rw.to_dict(job))


@app.route("/api/jobs")
def api_jobs_list():
    from src import reextract_worker as rw
    return jsonify({"jobs": [rw.to_dict(j) for j in rw.list_jobs(limit=30)]})


@app.route("/api/jobs/<job_id>/cancel", methods=["POST"])
def api_job_cancel(job_id: str):
    from src import reextract_worker as rw
    if not rw.cancel(job_id):
        abort(400, "job not cancellable (already running or not found)")
    return jsonify({"ok": True, "job_id": job_id})


# ===================== analysis & review & experiment ============================


def _facts_per_annotator_from_disk(doc_id: str) -> dict[str, list[dict]]:
    out: dict[str, list[dict]] = {}
    if not FACTS.exists():
        return out
    for sub in sorted(FACTS.iterdir()):
        if not sub.is_dir():
            continue
        fp = sub / f"{doc_id}.json"
        if not fp.exists():
            continue
        try:
            doc = _load_json(fp)
        except Exception:
            continue
        out[doc.get("annotator") or sub.name] = doc.get("facts", [])
    return out


@app.route("/api/similarity_matrix/<doc_id>")
def api_similarity_matrix(doc_id: str):
    """Poster Fig. 2 (left): full cosine matrix between two annotators.

    Prefers the persisted Phase-2 conflicts file (so matched cells agree
    with the pipeline); falls back to raw fact files + on-the-fly Hungarian.
    """
    from src.annotator_compare import similarity_matrix
    cpath = _conflicts_path(doc_id)
    aligned_pairs = None
    if cpath.exists():
        confl = _load_json(cpath)
        fpa = confl.get("facts_per_annotator", {})
        aligned_pairs = confl.get("aligned_pairs", [])
    else:
        fpa = _facts_per_annotator_from_disk(doc_id)
    annotators = sorted(fpa.keys())
    if len(annotators) < 2:
        return jsonify({"doc_id": doc_id, "annotators": annotators,
                        "note": "need at least 2 annotators with facts on disk"})
    a = request.args.get("a") or annotators[0]
    b = request.args.get("b") or next(x for x in annotators if x != a)
    if a not in fpa or b not in fpa:
        abort(404, f"annotator not found; available: {annotators}")
    threshold = float(request.args.get("threshold", 0.78))
    out = similarity_matrix(fpa[a], fpa[b], annotator_a=a, annotator_b=b,
                            threshold=threshold, aligned_pairs=aligned_pairs)
    out["doc_id"] = doc_id
    out["annotators"] = annotators
    return jsonify(out)


@app.route("/api/iaa/<doc_id>")
def api_iaa(doc_id: str):
    """Poster-style IAA summary per annotator pair (from the conflicts file)."""
    from src.annotator_compare import iaa_summary
    cpath = _conflicts_path(doc_id)
    if not cpath.exists():
        return jsonify({"doc_id": doc_id, "annotators": [], "pairs": [],
                        "note": "no Phase-2 output for this doc yet"})
    out = iaa_summary(_load_json(cpath))
    out["doc_id"] = doc_id
    return jsonify(out)


# ---------------------- conflict review queue ----------------------

REVIEWS = DATA / "reviews"


@app.route("/api/guideline_rules")
def api_guideline_rules():
    from src.review_store import parse_guideline_rules
    version = request.args.get("version", "v1")
    if not _GUIDELINE_VERSION_RE.match(version):
        abort(400, "invalid version")
    return jsonify({"version": version,
                    "rules": parse_guideline_rules(version, PROMPTS)})


@app.route("/api/review/<doc_id>", methods=["GET", "POST"])
def api_review(doc_id: str):
    from src.review_store import load_reviews, save_review
    if request.method == "GET":
        return jsonify({"doc_id": doc_id, "reviews": load_reviews(REVIEWS, doc_id)})
    payload = request.get_json(silent=True) or {}
    pair_key = (payload.get("pair_key") or "").strip()
    if not pair_key:
        abort(400, "pair_key is required")
    try:
        out = save_review(REVIEWS, doc_id, pair_key, payload)
    except ValueError as exc:
        abort(400, str(exc))
    return jsonify({"ok": True, "doc_id": doc_id, **out})


@app.route("/api/review_summary")
def api_review_summary():
    from src.review_store import summarize_reviews
    version = request.args.get("version", "v1")
    if not _GUIDELINE_VERSION_RE.match(version):
        abort(400, "invalid version")
    return jsonify(summarize_reviews(REVIEWS, prompts_dir=PROMPTS,
                                     guideline_version=version))


# ---------------------- one-click experiment pipeline ----------------------


@app.route("/api/run_pipeline", methods=["POST"])
def api_run_pipeline():
    """Closed loop in one job: Phase-1 (versioned facts root) -> Phase-2
    (versioned conflicts file). Defaults to the docs that already have a
    baseline Phase-2 result so v1 vs v2 stays apples-to-apples."""
    from scripts.run_phase2 import discover_all_doc_ids
    payload = request.get_json(silent=True) or {}
    version = payload.get("guideline_version") or "v1"
    if not _GUIDELINE_VERSION_RE.match(version):
        abort(400, "invalid guideline_version")
    models = payload.get("models") or ["qwen3.5:4b"]
    doc_ids = payload.get("doc_ids")
    if not doc_ids:
        doc_ids = sorted(_docs_with_conflicts()) or discover_all_doc_ids(FACTS)
    doc_paths = [str(PARSED / f"{d}.json")
                 for d in doc_ids if (PARSED / f"{d}.json").exists()]
    if not doc_paths:
        abort(400, "no parsed docs for the requested doc_ids")
    phase2_params = {
        "skip_layer2": bool(payload.get("skip_layer2", True)),
        "align_threshold": float(payload.get("align_threshold", 0.78)),
        "redundancy_cosine": float(payload.get("redundancy_cosine", 0.95)),
        "merge_threshold": float(payload.get("merge_threshold", 0.78)),
        "layer2_model": payload.get("layer2_model", "qwen3.5:4b"),
        "layer2_url": payload.get("layer2_url", os.environ.get("OLLAMA_URL", "http://localhost:11434")),
        "skip_extract": bool(payload.get("skip_extract", False)),
    }
    from src import reextract_worker as rw
    try:
        job = rw.enqueue_pipeline(
            version, models=models, doc_paths=doc_paths,
            phase2_params=phase2_params,
            label=f"experiment {version} ({len(models)}m × {len(doc_paths)}d → phase2)")
    except FileNotFoundError as exc:
        abort(400, str(exc))
    return jsonify(rw.to_dict(job)), 202


@app.route("/api/distribution_shift_agg")
def api_distribution_shift_agg():
    """Corpus-level v1 vs v2 distribution shift: sums label counts and
    Layer-1 rates over every doc that has BOTH conflicts files."""
    from src.distribution_shift import CONFLICT_LABELS
    v2 = request.args.get("v2")
    if not v2 or not _GUIDELINE_VERSION_RE.match(v2):
        abort(400, "?v2=<version> is required")
    totals = {"v1": {l: 0 for l in CONFLICT_LABELS},
              "v2": {l: 0 for l in CONFLICT_LABELS}}
    layer = {"v1": {"matched": 0, "layer2_calls": 0},
             "v2": {"matched": 0, "layer2_calls": 0}}
    ann_sets = {"v1": set(), "v2": set()}
    docs = []
    for p1 in sorted(CONFLICTS.glob("*.json")):
        if "__" in p1.stem:
            continue
        p2 = CONFLICTS / f"{p1.stem}__{v2}.json"
        if not p2.exists():
            continue
        try:
            d1, d2 = _load_json(p1), _load_json(p2)
        except Exception:
            continue
        docs.append(p1.stem)
        ann_sets["v1"].update(d1.get("annotators", []))
        ann_sets["v2"].update(d2.get("annotators", []))
        for key, d in (("v1", d1), ("v2", d2)):
            for l in CONFLICT_LABELS:
                totals[key][l] += int(d.get("label_counts", {}).get(l, 0))
            layer[key]["matched"] += sum(
                1 for pr in d.get("aligned_pairs", []) if pr.get("status") == "matched")
            layer[key]["layer2_calls"] += int(d.get("layer2_calls", 0))
    labels = list(CONFLICT_LABELS)
    v1_counts = [totals["v1"][l] for l in labels]
    v2_counts = [totals["v2"][l] for l in labels]
    def _rate(side):
        m = layer[side]["matched"]
        return round(1 - layer[side]["layer2_calls"] / m, 3) if m else None
    out = {
        "v1_label": "v1", "v2_label": v2, "docs": docs, "n_docs": len(docs),
        "labels": labels, "v1": v1_counts, "v2": v2_counts,
        "delta": [b - a for a, b in zip(v1_counts, v2_counts)],
        "delta_pct": [round((b - a) / a * 100, 1) if a else None
                      for a, b in zip(v1_counts, v2_counts)],
        "totals": {
            "v1": sum(v1_counts), "v2": sum(v2_counts),
            "delta": sum(v2_counts) - sum(v1_counts),
            "delta_pct": round((sum(v2_counts) - sum(v1_counts)) / sum(v1_counts) * 100, 1)
                         if sum(v1_counts) else None,
        },
        "layer1_rate": {"v1": _rate("v1"), "v2": _rate("v2")},
        "v1_annotators": sorted(ann_sets["v1"]),
        "v2_annotators": sorted(ann_sets["v2"]),
        "annotators_match": ann_sets["v1"] == ann_sets["v2"],
    }
    if not docs:
        out["note"] = (f"no doc has both a baseline and a '{v2}' conflicts file yet — "
                       f"run the experiment pipeline first")
    return jsonify(out)


# --- Neo4j custom conflict detection ("graph patching") --------------------


@app.route("/api/neo4j/status")
def api_neo4j_status():
    from src import neo4j_constraints as nc
    return jsonify(nc.status())


@app.route("/api/neo4j/constraints")
def api_neo4j_constraints():
    from src import neo4j_constraints as nc
    return jsonify({"constraints": nc.BUILTIN_CONSTRAINTS})


@app.route("/api/neo4j/export/<doc_id>", methods=["POST"])
def api_neo4j_export(doc_id: str):
    """Push the current doc's reified KG into Neo4j (wipe+reload for this doc)."""
    from src import neo4j_constraints as nc
    from src.neo4j_export import build_batches, export
    cpath = _conflicts_path(doc_id)
    if not cpath.exists():
        abort(404, f"no conflicts doc for {doc_id!r} (run Phase-2 first)")
    if not nc.driver_available():
        abort(400, "neo4j driver not installed (pip install neo4j)")
    doc = _load_json(cpath)
    batches = build_batches(doc, _variant_arg() or None)
    uri, user, pw = nc._conn()
    try:
        summary = export(batches, uri, user, pw, wipe=True)
    except Exception as exc:
        abort(502, f"neo4j export failed: {type(exc).__name__}: {exc}")
    return jsonify({"ok": True, "doc_id": doc_id, "summary": summary})


@app.route("/api/neo4j/constraint", methods=["POST"])
def api_neo4j_constraint():
    """Run a custom Cypher constraint; return violation rows (or materialize)."""
    from src import neo4j_constraints as nc
    payload = request.get_json(silent=True) or {}
    cypher = (payload.get("cypher") or "").strip()
    params = payload.get("params") or {}
    mode = payload.get("mode") or "report"
    if not cypher:
        abort(400, "cypher required")
    ok, reason = nc.validate_cypher(cypher, mode)
    if not ok:
        abort(400, reason)
    if not nc.driver_available():
        abort(400, "neo4j driver not installed (pip install neo4j)")
    try:
        result = nc.run_constraint(cypher, params, mode=mode)
    except ValueError as exc:
        abort(400, str(exc))
    except Exception as exc:
        abort(502, f"neo4j query failed: {type(exc).__name__}: {exc}")
    return jsonify(result)


def main() -> None:
    import argparse
    p = argparse.ArgumentParser()
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=5000)
    p.add_argument("--debug", action="store_true")
    args = p.parse_args()
    app.run(host=args.host, port=args.port, debug=args.debug)


if __name__ == "__main__":
    main()
