"""
Phase-4 — Background re-extraction & Phase-2 worker.

A single daemon thread serves a single queue. Two job kinds:

  - "reextract"  : iterate (model × doc), call src.extractor.run_doc().
                   Used by POST /api/guideline, POST /api/run_phase1,
                   POST /api/run_matrix, and span re-extracts via
                   POST /api/upload_text(extract=true).

  - "phase2"     : iterate docs, call scripts.run_phase2.run() per doc.
                   Used by POST /api/run_phase2. Produces
                   data/conflicts/<doc>.json files the UI consumes.

We keep one worker thread + one queue because the GPU is the bottleneck
for "reextract" and CPU/Ollama-call is the bottleneck for "phase2";
running them serially is the same hardware contention envelope as the
analyst kicking them off manually. Future: add a second pool just for
Phase-2 if Layer-2 latency becomes the gating factor.

Job lifecycle: queued → running → done (or failed).
"""

from __future__ import annotations

import json
import logging
import queue
import re
import threading
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Iterable

log = logging.getLogger(__name__)

REPO_ROOT = Path(__file__).resolve().parent.parent
PROMPTS_DIR = REPO_ROOT / "prompts"
FACTS_ROOT = REPO_ROOT / "data" / "facts"
PARSED_ROOT = REPO_ROOT / "data" / "parsed"
CONFLICTS_ROOT = REPO_ROOT / "data" / "conflicts"


# ---------------------------------------------------------------------------
# Job model
# ---------------------------------------------------------------------------


@dataclass
class Job:
    job_id: str
    kind: str                          # "reextract" | "phase2"
    # Reextract-specific
    guideline_text: str = ""
    guideline_version: str = ""
    models: list[str] = field(default_factory=list)
    doc_paths: list[str] = field(default_factory=list)
    # Phase-2-specific
    doc_ids: list[str] = field(default_factory=list)
    phase2_params: dict = field(default_factory=dict)
    # Shared
    status: str = "queued"
    progress: dict = field(default_factory=lambda: {"done": 0, "total": 0})
    results: list[dict] = field(default_factory=list)
    error: str | None = None
    label: str = ""                    # short human label for UI listing
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    started_at: str | None = None
    finished_at: str | None = None


_JOBS: dict[str, Job] = {}
_QUEUE: "queue.Queue[Job]" = queue.Queue()
_WORKER: threading.Thread | None = None
_LOCK = threading.Lock()


def _safe_model_dir(name: str) -> str:
    return name.replace(":", "_").replace("/", "_").replace(".", "_")


# ---------------------------------------------------------------------------
# Reextract job execution
# ---------------------------------------------------------------------------


def _run_reextract(job: Job, *, run_doc_fn: Callable | None = None,
                   base_url: str = "http://localhost:11434") -> None:
    job.status = "running"
    job.started_at = datetime.now(timezone.utc).isoformat()

    if run_doc_fn is None:
        from src.extractor import run_doc as run_doc_fn  # type: ignore

    guideline_path = PROMPTS_DIR / f"extract_{job.guideline_version}.md"
    try:
        guideline_path.parent.mkdir(parents=True, exist_ok=True)
        if not guideline_path.exists() or guideline_path.read_text(encoding="utf-8") != job.guideline_text:
            guideline_path.write_text(job.guideline_text, encoding="utf-8")
    except OSError as exc:
        job.status = "failed"
        job.error = f"failed to write guideline file: {exc}"
        job.finished_at = datetime.now(timezone.utc).isoformat()
        return

    job.progress["total"] = len(job.models) * len(job.doc_paths)

    for model in job.models:
        for parsed_path in job.doc_paths:
            cell_result: dict = {
                "model": model, "doc_path": parsed_path,
                "status": "pending", "fact_count": 0, "error": None,
            }
            try:
                t0 = time.perf_counter()
                out_path = run_doc_fn(
                    model_name=model,
                    parsed_path=parsed_path,
                    guideline_version=job.guideline_version,
                    base_url=base_url,
                    ensure_solo=True,
                    unload_after=False,
                )
                fc = 0
                try:
                    fc = int(json.loads(Path(out_path).read_text("utf-8")).get("fact_count", 0))
                except Exception:
                    pass
                cell_result.update({
                    "status": "ok",
                    "fact_count": fc,
                    "elapsed_s": round(time.perf_counter() - t0, 2),
                    "out_path": str(out_path),
                })
            except Exception as exc:
                cell_result.update({"status": "error", "error": f"{type(exc).__name__}: {exc}"})
                log.warning("Re-extract cell failed (%s × %s): %s", model, parsed_path, exc)
            job.results.append(cell_result)
            job.progress["done"] = len(job.results)

    n_err = sum(1 for r in job.results if r["status"] == "error")
    job.status = "done" if n_err == 0 else "failed"
    if job.status == "failed" and job.error is None:
        job.error = f"{n_err}/{len(job.results)} cells failed"
    job.finished_at = datetime.now(timezone.utc).isoformat()


# ---------------------------------------------------------------------------
# Phase-2 job execution
# ---------------------------------------------------------------------------


def _run_phase2(job: Job, *, run_fn: Callable | None = None) -> None:
    """Run scripts.run_phase2.run() per doc_id. Aggregates label counts."""
    job.status = "running"
    job.started_at = datetime.now(timezone.utc).isoformat()

    if run_fn is None:
        from scripts.run_phase2 import run as run_fn  # type: ignore

    params = dict(job.phase2_params)
    skip_layer2 = bool(params.pop("skip_layer2", True))
    facts_root = Path(params.pop("facts_root", FACTS_ROOT))
    parsed_root = Path(params.pop("parsed_root", PARSED_ROOT))
    out_root = Path(params.pop("out_root", CONFLICTS_ROOT))

    job.progress["total"] = len(job.doc_ids)

    for doc_id in job.doc_ids:
        cell: dict = {"doc_id": doc_id, "status": "pending"}
        try:
            t0 = time.perf_counter()
            out_path = run_fn(
                doc_id,
                facts_root=facts_root,
                parsed_root=parsed_root,
                out_root=out_root,
                align_threshold=float(params.get("align_threshold", 0.78)),
                redundancy_cosine=float(params.get("redundancy_cosine", 0.95)),
                merge_threshold=float(params.get("merge_threshold", 0.78)),
                layer2_model=params.get("layer2_model", "qwen3.5:4b"),
                skip_layer2=skip_layer2,
                layer2_url=params.get("layer2_url", "http://localhost:11434"),
            )
            confl = json.loads(Path(out_path).read_text(encoding="utf-8"))
            cell.update({
                "status": "ok",
                "elapsed_s": round(time.perf_counter() - t0, 2),
                "label_counts": confl.get("label_counts", {}),
                "out_path": str(out_path),
            })
        except Exception as exc:
            cell.update({"status": "error", "error": f"{type(exc).__name__}: {exc}"})
            log.warning("Phase-2 doc failed (%s): %s", doc_id, exc)
        job.results.append(cell)
        job.progress["done"] = len(job.results)

    n_err = sum(1 for r in job.results if r["status"] == "error")
    job.status = "done" if n_err == 0 else "failed"
    if job.status == "failed" and job.error is None:
        job.error = f"{n_err}/{len(job.results)} docs failed"
    job.finished_at = datetime.now(timezone.utc).isoformat()


# ---------------------------------------------------------------------------
# Worker loop
# ---------------------------------------------------------------------------


def _worker_loop() -> None:
    while True:
        job = _QUEUE.get()
        try:
            if job.kind == "phase2":
                _run_phase2(job)
            else:
                _run_reextract(job)
        except Exception as exc:                          # pragma: no cover
            log.exception("Worker crashed on job %s", job.job_id)
            job.status = "failed"
            job.error = f"worker crash: {exc}"
        finally:
            _QUEUE.task_done()


def _ensure_worker_started() -> None:
    global _WORKER
    with _LOCK:
        if _WORKER is None or not _WORKER.is_alive():
            _WORKER = threading.Thread(target=_worker_loop, daemon=True, name="va-worker")
            _WORKER.start()
            log.info("Worker thread started.")


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def enqueue(
    guideline_text: str,
    *,
    models: Iterable[str],
    doc_paths: Iterable[str],
    label: str = "",
) -> Job:
    """Submit a re-extraction job."""
    job_id = uuid.uuid4().hex[:12]
    safe_token = re.sub(r"[^A-Za-z0-9_]", "_", job_id)
    job = Job(
        job_id=job_id,
        kind="reextract",
        guideline_text=guideline_text,
        guideline_version=f"v2_{safe_token}",
        models=list(models),
        doc_paths=list(doc_paths),
        label=label or f"re-extract ({len(list(models))}×{len(list(doc_paths))} cells)",
    )
    _JOBS[job_id] = job
    _ensure_worker_started()
    _QUEUE.put(job)
    return job


def enqueue_reextract_with_version(
    guideline_version: str,
    *,
    models: Iterable[str],
    doc_paths: Iterable[str],
    label: str = "",
) -> Job:
    """Same as `enqueue` but loads guideline text from an existing on-disk version.

    Used by /api/run_phase1 and /api/run_matrix — they reference an existing
    guideline rather than passing a fresh body. Writing the same file back
    is a no-op (the _run_reextract code checks before writing).
    """
    guideline_path = PROMPTS_DIR / f"extract_{guideline_version}.md"
    if not guideline_path.exists():
        raise FileNotFoundError(f"guideline {guideline_version!r} not found at {guideline_path}")
    text = guideline_path.read_text(encoding="utf-8")

    job_id = uuid.uuid4().hex[:12]
    job = Job(
        job_id=job_id,
        kind="reextract",
        guideline_text=text,
        guideline_version=guideline_version,
        models=list(models),
        doc_paths=list(doc_paths),
        label=label or f"phase-1 batch ({guideline_version}, {len(list(models))}m × {len(list(doc_paths))}d)",
    )
    _JOBS[job_id] = job
    _ensure_worker_started()
    _QUEUE.put(job)
    return job


def enqueue_phase2(
    *,
    doc_ids: Iterable[str],
    params: dict | None = None,
    label: str = "",
) -> Job:
    """Submit a Phase-2 alignment + conflict-detection job covering N docs."""
    job_id = uuid.uuid4().hex[:12]
    job = Job(
        job_id=job_id,
        kind="phase2",
        doc_ids=list(doc_ids),
        phase2_params=dict(params or {}),
        label=label or f"phase-2 batch ({len(list(doc_ids))} docs)",
    )
    _JOBS[job_id] = job
    _ensure_worker_started()
    _QUEUE.put(job)
    return job


def get_job(job_id: str) -> Job | None:
    return _JOBS.get(job_id)


def list_jobs(limit: int | None = 30) -> list[Job]:
    items = sorted(_JOBS.values(), key=lambda j: j.created_at, reverse=True)
    return items[:limit] if limit else items


def cancel(job_id: str) -> bool:
    j = _JOBS.get(job_id)
    if j is None:
        return False
    if j.status == "queued":
        j.status = "failed"
        j.error = "cancelled by user"
        j.finished_at = datetime.now(timezone.utc).isoformat()
        return True
    return False


def to_dict(job: Job) -> dict:
    return {
        "job_id": job.job_id,
        "kind": job.kind,
        "label": job.label,
        "status": job.status,
        "progress": job.progress,
        "guideline_version": job.guideline_version,
        "models": job.models,
        "doc_paths": job.doc_paths,
        "doc_ids": job.doc_ids,
        "phase2_params": job.phase2_params,
        "results": job.results,
        "error": job.error,
        "created_at": job.created_at,
        "started_at": job.started_at,
        "finished_at": job.finished_at,
    }
