"""
Ollama dispatcher — Phase-1 atomic-fact extraction.

Public surface:

    extract(model_name, parsed_doc, *, guideline_version="v1", ...) -> list[AtomicFact]
    unload(model_name, *, base_url="http://localhost:11434") -> None
    load_parsed_doc(path) -> ParsedDocument
    run_doc(model_name, parsed_doc, output_dir, **kwargs) -> Path

CLI:

    python -m src.extractor extract --model qwen3.5:4b \
        --doc data/parsed/train-000000.json \
        --out data/facts

Design constraints (see PROJECT_STATE.md §1):

* RTX 3060M / 6 GB VRAM ⇒ only ONE 4B-class model resident at a time. Before
  each run we POST `keep_alive=0` to any other model currently loaded
  (`/api/ps`). At the end of `extract()` we also unload, so the next call
  with a different model starts from a clean GPU.
* Per-section chunking (each recital / each article as its own LLM call)
  keeps the input under ~1K tokens. This is the empirical sweet-spot for
  4B-class models established in the preceding benchmarking project; see
  proposal §Phase 2 architectural justification.
* The extraction prompt is the file `prompts/extract_<version>.md` and its
  `<guideline>…</guideline>` block is the **v1 baseline** for the Phase-4
  v1→v2 distribution-shift experiment. Do not bake guideline text into
  this dispatcher; always read it from disk so analysts can edit it
  through the VA tool (Phase-3) without touching Python.
* Output of every LLM call is validated against the AtomicFact pydantic
  schema. Schema/quote failures trigger a bounded retry loop with the
  parse error fed back to the model.
* Each fact's `source_quote` is anchored back to character offsets in the
  appropriate section container (`preamble_text` for recitals,
  `enacting_text` for articles) via RapidFuzz, producing a `ProseLocator`.
"""

from __future__ import annotations

import hashlib
import json
import logging
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterator

import requests
from pydantic import ValidationError
from rapidfuzz import fuzz

# Resolve project root so `python src/extractor.py` and `python -m src.extractor`
# both work without a hard dependency on cwd.
_THIS = Path(__file__).resolve()
_PROJECT_ROOT = _THIS.parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from src.schema import (  # noqa: E402
    AtomicFact,
    ParsedDocument,
    ProseLocator,
    SourceType,
)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

DEFAULT_BASE_URL = "http://localhost:11434"
DEFAULT_KEEP_ALIVE = "5m"  # while a run is in flight
UNLOAD_KEEP_ALIVE = 0  # sentinel: unload immediately
DEFAULT_TIMEOUT_S = 600  # legal recitals are slow on a 4B Q4_K_M
OFFSET_MIN_SCORE = 70.0  # below this we drop the fact rather than mis-locate it

REQUIRED_FIELDS = ("subject", "predicate", "object", "natural_language", "source_quote")


# ---------------------------------------------------------------------------
# Section enumeration
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Section:
    """One LLM-sized chunk of a ParsedDocument."""

    section_path: str           # e.g. "preamble.recitals[3]" or "enacting.article_1"
    text: str                   # the chunk fed to the model (verbatim slice)
    container_text: str         # the parent stream the offsets refer to
    base_offset: int            # text == container_text[base_offset : base_offset + len(text)]


def enumerate_sections(doc: ParsedDocument) -> Iterator[Section]:
    """Yield the units of extraction for v1: each recital, each article.

    We skip the citation block ("Having regard to …") because guideline §5
    instructs the model not to extract from it. We also skip the closing
    formula for the same reason. If a future guideline wants to revisit
    those, add them here — the prompt will still apply guideline §5.
    """
    for i, recital in enumerate(doc.recitals):
        # Slice rather than rebuilding the string so character offsets line
        # up exactly with `preamble_text`.
        text = doc.preamble_text[recital.char_start:recital.char_end]
        if text.strip():
            yield Section(
                section_path=f"preamble.recitals[{i}]",
                text=text,
                container_text=doc.preamble_text,
                base_offset=recital.char_start,
            )

    for article in doc.articles:
        text = doc.enacting_text[article.char_start:article.char_end]
        if text.strip():
            yield Section(
                section_path=f"enacting.article_{article.number}",
                text=text,
                container_text=doc.enacting_text,
                base_offset=article.char_start,
            )


# ---------------------------------------------------------------------------
# Prompt loading
# ---------------------------------------------------------------------------


def load_prompt_template(guideline_version: str = "v1",
                         prompt_path: Path | None = None) -> str:
    """Read the markdown prompt template from disk.

    The template uses sentinel strings (not str.format placeholders) so that
    legal section text containing literal `{` `}` characters does not break
    substitution.
    """
    if prompt_path is None:
        prompt_path = _PROJECT_ROOT / "prompts" / f"extract_{guideline_version}.md"
    if not prompt_path.exists():
        raise FileNotFoundError(
            f"Prompt template not found: {prompt_path}. "
            f"Did you write prompts/extract_{guideline_version}.md ?"
        )
    return prompt_path.read_text(encoding="utf-8")


def render_prompt(template: str, *, doc_title: str, section: Section,
                  suppress_thinking: bool = True) -> str:
    """Substitute the three sentinels in the template.

    `suppress_thinking` appends `/no_think` — the Qwen3 inline switch that
    forces non-reasoning mode. This is a dispatcher-level adapter, kept
    OUT of the markdown template so the guideline stays model-agnostic
    (Gemma3 / Phi4-mini ignore the token harmlessly).
    """
    out = template
    out = out.replace("<<DOC_TITLE>>", doc_title)
    out = out.replace("<<SECTION_PATH>>", section.section_path)
    out = out.replace("<<SECTION_TEXT>>", section.text)
    if suppress_thinking:
        out = out + "\n\n/no_think"
    return out


# ---------------------------------------------------------------------------
# Ollama HTTP wrappers
# ---------------------------------------------------------------------------


def _post(base_url: str, path: str, payload: dict, *, timeout: float = DEFAULT_TIMEOUT_S) -> dict:
    url = f"{base_url.rstrip('/')}{path}"
    resp = requests.post(url, json=payload, timeout=timeout)
    resp.raise_for_status()
    return resp.json()


def _get(base_url: str, path: str, *, timeout: float = 10) -> dict:
    url = f"{base_url.rstrip('/')}{path}"
    resp = requests.get(url, timeout=timeout)
    resp.raise_for_status()
    return resp.json()


def list_loaded_models(base_url: str = DEFAULT_BASE_URL) -> list[str]:
    """Names of models currently resident in VRAM, per Ollama `/api/ps`."""
    try:
        body = _get(base_url, "/api/ps")
    except requests.RequestException as exc:
        logger.warning("Could not reach Ollama at %s (/api/ps): %s", base_url, exc)
        return []
    return [m["name"] for m in body.get("models", [])]


def unload(model_name: str, *, base_url: str = DEFAULT_BASE_URL) -> None:
    """Evict a model from VRAM by issuing a no-op generate with keep_alive=0."""
    try:
        _post(base_url, "/api/generate",
              {"model": model_name, "prompt": "", "keep_alive": UNLOAD_KEEP_ALIVE},
              timeout=30)
        logger.info("Unloaded model %s", model_name)
    except requests.RequestException as exc:
        logger.warning("Failed to unload %s: %s", model_name, exc)


def _unload_others(keep: str, base_url: str) -> None:
    """Free VRAM by evicting every loaded model that is not `keep`."""
    for name in list_loaded_models(base_url):
        # Ollama returns names like "qwen2.5:4b-instruct-q4_K_M" — match exactly.
        if name != keep:
            unload(name, base_url=base_url)


def _generate_json(model_name: str,
                   prompt: str,
                   *,
                   base_url: str,
                   keep_alive: str | int = DEFAULT_KEEP_ALIVE,
                   temperature: float = 0.0,
                   num_ctx: int = 4096) -> str:
    """Call /api/generate with format=json. Returns the raw JSON string."""
    payload = {
        "model": model_name,
        "prompt": prompt,
        "stream": False,
        "format": "json",
        "keep_alive": keep_alive,
        # Qwen3 / DeepSeek-style hybrid-reasoning models default to thinking
        # mode, which sinks tokens into <think>…</think> and leaves
        # `response` empty under format=json. We're a constrained-extraction
        # task — no chain-of-thought benefit — so turn it off.
        "think": False,
        "options": {
            # Deterministic-ish: phase-4 distribution comparison needs reproducibility.
            "temperature": temperature,
            "num_ctx": num_ctx,
        },
    }
    body = _post(base_url, "/api/generate", payload)
    raw = body.get("response", "")
    if not raw.strip():
        # Surface what Ollama actually sent back so the user can tell
        # "empty response" from "model is paraphrasing instead of JSON".
        logger.warning(
            "Empty response from %s (thinking=%r, prompt_eval_count=%s, eval_count=%s). "
            "If this persists, check that `think=False` is honoured by your Ollama version.",
            model_name, body.get("thinking", "<absent>"),
            body.get("prompt_eval_count"), body.get("eval_count"),
        )
    return raw


# ---------------------------------------------------------------------------
# Output parsing + offset recovery
# ---------------------------------------------------------------------------


class ExtractionError(Exception):
    """Raised when the model's response cannot be coerced into facts."""


def _parse_facts_response(raw: str) -> list[dict]:
    """Parse the model's raw response into a list of fact dicts.

    Enforces:
      * Top-level is a JSON object with key "facts".
      * "facts" is a list (possibly empty).
      * Each element has all REQUIRED_FIELDS as non-empty strings.

    On any violation, raises ExtractionError with a message suitable for
    feeding back to the model as a corrective hint.
    """
    try:
        obj = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ExtractionError(f"Response was not valid JSON: {exc.msg}") from exc

    if not isinstance(obj, dict) or "facts" not in obj:
        raise ExtractionError(
            'Response JSON must be an object with a top-level "facts" key.'
        )
    facts = obj["facts"]
    if not isinstance(facts, list):
        raise ExtractionError('"facts" must be a JSON array.')

    cleaned: list[dict] = []
    for i, f in enumerate(facts):
        if not isinstance(f, dict):
            raise ExtractionError(f"facts[{i}] is not a JSON object.")
        for field in REQUIRED_FIELDS:
            if field not in f:
                raise ExtractionError(f'facts[{i}] is missing required field "{field}".')
            if not isinstance(f[field], str) or not f[field].strip():
                raise ExtractionError(
                    f'facts[{i}]."{field}" must be a non-empty string.'
                )
        cleaned.append({k: f[k].strip() for k in REQUIRED_FIELDS})
    return cleaned


def _recover_offsets(quote: str, section: Section) -> tuple[int, int, float, str]:
    """RapidFuzz-anchor `quote` inside `section.text`, then translate to
    container offsets.

    Returns (char_start, char_end, score, matched_substring).
    char_start/char_end are absolute positions inside section.container_text.
    """
    # Cheap exact match first — avoids fuzzy-matching jitter when the model
    # quoted verbatim, which it should ~95 % of the time given guideline §4.
    idx = section.text.find(quote)
    if idx >= 0:
        cs = section.base_offset + idx
        ce = cs + len(quote)
        return cs, ce, 100.0, quote

    # Fuzzy fallback for whitespace/punctuation drift.
    align = fuzz.partial_ratio_alignment(quote, section.text)
    if align is None:
        return -1, -1, 0.0, ""
    score = float(align.score)
    if score < OFFSET_MIN_SCORE:
        return -1, -1, score, ""
    ds, de = align.dest_start, align.dest_end
    cs = section.base_offset + ds
    ce = section.base_offset + de
    matched = section.container_text[cs:ce]
    return cs, ce, score, matched


def _fact_id(model: str, doc_id: str, section_path: str, index: int) -> str:
    h = hashlib.sha1()
    h.update(f"{model}|{doc_id}|{section_path}|{index}".encode("utf-8"))
    return h.hexdigest()[:16]


def _build_atomic_fact(raw: dict,
                       *,
                       model_name: str,
                       doc: ParsedDocument,
                       guideline_version: str,
                       section: Section,
                       index: int) -> AtomicFact | None:
    """Convert a validated raw dict into an AtomicFact, or return None if the
    quote could not be anchored (which means we cannot trust the provenance)."""
    cs, ce, score, matched = _recover_offsets(raw["source_quote"], section)
    if cs < 0:
        logger.warning(
            "Dropped fact (quote not located in %s, fuzzy score=%.1f): %r",
            section.section_path, score, raw["source_quote"][:80],
        )
        return None

    locator = ProseLocator(
        section_path=section.section_path,
        char_start=cs,
        char_end=ce,
        quote=matched,
    )
    return AtomicFact(
        fact_id=_fact_id(model_name, doc.celex, section.section_path, index),
        doc_id=doc.celex,
        annotator=model_name,
        guideline_version=guideline_version,
        subject=raw["subject"],
        predicate=raw["predicate"],
        object=raw["object"],
        natural_language=raw["natural_language"],
        source_locator=locator,
        extra={
            "anchor_score": score,
            "model_quote": raw["source_quote"],
        },
    )


# ---------------------------------------------------------------------------
# Main extraction loop
# ---------------------------------------------------------------------------


def _extract_one_section(model_name: str,
                         doc: ParsedDocument,
                         section: Section,
                         template: str,
                         *,
                         guideline_version: str,
                         base_url: str,
                         retries: int) -> list[AtomicFact]:
    """Run the prompt → JSON → validate → anchor pipeline for one section."""
    prompt = render_prompt(template, doc_title=doc.title, section=section)
    last_error: str | None = None

    for attempt in range(retries + 1):
        try_prompt = prompt
        if last_error is not None:
            # Feed the previous parse error back so the model can self-correct.
            try_prompt = (
                f"{prompt}\n\nYour previous response was rejected: {last_error}\n"
                "Return the corrected JSON object only."
            )
        try:
            raw = _generate_json(model_name, try_prompt, base_url=base_url)
            cleaned = _parse_facts_response(raw)
        except ExtractionError as exc:
            last_error = str(exc)
            logger.info(
                "[%s] %s — attempt %d/%d rejected: %s",
                model_name, section.section_path, attempt + 1, retries + 1, exc,
            )
            continue
        except requests.RequestException as exc:
            # Network-level failure: do not waste retries on this.
            raise RuntimeError(
                f"Ollama request failed on section {section.section_path}: {exc}"
            ) from exc

        # Success path — build AtomicFact objects.
        facts: list[AtomicFact] = []
        for i, raw_fact in enumerate(cleaned):
            try:
                f = _build_atomic_fact(
                    raw_fact,
                    model_name=model_name,
                    doc=doc,
                    guideline_version=guideline_version,
                    section=section,
                    index=i,
                )
            except ValidationError as exc:
                logger.warning(
                    "Skipping fact %d in %s: %s",
                    i, section.section_path, exc,
                )
                continue
            if f is not None:
                facts.append(f)
        logger.info(
            "[%s] %s — %d facts extracted (attempt %d).",
            model_name, section.section_path, len(facts), attempt + 1,
        )
        return facts

    logger.error(
        "[%s] %s — all %d attempts failed (last error: %s).",
        model_name, section.section_path, retries + 1, last_error,
    )
    return []


def extract(model_name: str,
            parsed_doc: ParsedDocument,
            *,
            guideline_version: str = "v1",
            base_url: str = DEFAULT_BASE_URL,
            retries: int = 2,
            ensure_solo: bool = True,
            unload_after: bool = False,
            prompt_path: Path | None = None) -> list[AtomicFact]:
    """Extract atomic facts from one parsed document using one Ollama model.

    Parameters
    ----------
    model_name
        Ollama model tag exactly as `ollama list` shows it
        (e.g. "qwen2.5:4b-instruct-q4_K_M").
    parsed_doc
        A `ParsedDocument` (typically loaded from `data/parsed/<id>.json`).
    guideline_version
        Selects `prompts/extract_<version>.md`. Stored on every fact so
        the Phase-4 v1→v2 comparison can group by guideline.
    base_url
        Ollama API root. Default is local.
    retries
        Per-section retry budget for schema failures.
    ensure_solo
        If True, unload every other model currently in VRAM before
        starting. Required on 6 GB hardware when switching between the
        three 4B models.
    unload_after
        If True, also unload `model_name` at the end. Set this when the
        next pipeline step (e.g. another model's extraction or the Phase-2
        Layer-2 arbitrator) needs the VRAM.
    prompt_path
        Override the default `prompts/extract_<version>.md` location.
    """
    template = load_prompt_template(guideline_version, prompt_path)

    if ensure_solo:
        _unload_others(keep=model_name, base_url=base_url)

    all_facts: list[AtomicFact] = []
    for section in enumerate_sections(parsed_doc):
        all_facts.extend(_extract_one_section(
            model_name, parsed_doc, section, template,
            guideline_version=guideline_version,
            base_url=base_url,
            retries=retries,
        ))

    if unload_after:
        unload(model_name, base_url=base_url)

    logger.info(
        "Done: %s on %s → %d facts (guideline=%s).",
        model_name, parsed_doc.celex, len(all_facts), guideline_version,
    )
    return all_facts


# ---------------------------------------------------------------------------
# I/O helpers
# ---------------------------------------------------------------------------


def load_parsed_doc(path: Path | str) -> ParsedDocument:
    """Load a ParsedDocument from one of `data/parsed/*.json`.

    The file format produced by `scripts.run_dry_run` wraps the doc as
    `{"document": {...}, "linearized_table_rows": [...]}`. We accept both
    shapes for robustness.
    """
    path = Path(path)
    raw = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(raw, dict) and "document" in raw:
        doc_payload = raw["document"]
    else:
        doc_payload = raw
    return ParsedDocument.model_validate(doc_payload)


def write_facts(facts: list[AtomicFact],
                *,
                model_name: str,
                doc_id: str,
                guideline_version: str,
                out_root: Path | str) -> Path:
    """Persist facts to `<out_root>/<model_safe>/<doc_id>.json`."""
    safe_model = model_name.replace(":", "_").replace("/", "_")
    out_dir = Path(out_root) / safe_model
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"{doc_id}.json"
    payload = {
        "doc_id": doc_id,
        "annotator": model_name,
        "guideline_version": guideline_version,
        "fact_count": len(facts),
        "facts": [f.model_dump(mode="json") for f in facts],
    }
    out_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False),
                        encoding="utf-8")
    return out_path


def run_doc(model_name: str,
            parsed_path: Path | str,
            *,
            out_root: Path | str = None,
            **kwargs: Any) -> Path:
    """Convenience: load → extract → write. Returns the output path."""
    if out_root is None:
        out_root = _PROJECT_ROOT / "data" / "facts"
    doc = load_parsed_doc(parsed_path)
    guideline_version = kwargs.get("guideline_version", "v1")
    facts = extract(model_name, doc, **kwargs)
    return write_facts(
        facts,
        model_name=model_name,
        doc_id=doc.celex,
        guideline_version=guideline_version,
        out_root=out_root,
    )


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _build_cli():
    import typer
    app = typer.Typer(add_completion=False, help="Phase-1 atomic fact extractor (Ollama).")

    @app.command("extract")
    def cli_extract(
        model: str = typer.Option(..., "--model", "-m",
                                  help="Ollama model tag, e.g. qwen2.5:4b-instruct-q4_K_M."),
        doc: Path = typer.Option(..., "--doc", "-d",
                                 help="Path to a ParsedDocument JSON in data/parsed/."),
        out: Path = typer.Option(_PROJECT_ROOT / "data" / "facts", "--out", "-o",
                                 help="Root directory for fact JSON output."),
        guideline_version: str = typer.Option("v1", "--guideline", "-g"),
        retries: int = typer.Option(2, "--retries"),
        base_url: str = typer.Option(DEFAULT_BASE_URL, "--base-url"),
        no_solo: bool = typer.Option(False, "--no-solo",
                                     help="Do NOT evict other models first (use if you know VRAM is free)."),
        unload_after: bool = typer.Option(False, "--unload-after",
                                          help="Unload the model after the run finishes."),
    ):
        """Extract atomic facts from one parsed document."""
        logging.basicConfig(
            level=logging.INFO,
            format="%(asctime)s [%(levelname)s] %(message)s",
        )
        out_path = run_doc(
            model_name=model,
            parsed_path=doc,
            out_root=out,
            guideline_version=guideline_version,
            retries=retries,
            base_url=base_url,
            ensure_solo=not no_solo,
            unload_after=unload_after,
        )
        typer.echo(f"Wrote {out_path}")

    @app.command("unload")
    def cli_unload(
        model: str = typer.Option(..., "--model", "-m"),
        base_url: str = typer.Option(DEFAULT_BASE_URL, "--base-url"),
    ):
        """Evict a model from VRAM."""
        unload(model, base_url=base_url)

    @app.command("ps")
    def cli_ps(base_url: str = typer.Option(DEFAULT_BASE_URL, "--base-url")):
        """List models currently resident in VRAM."""
        for name in list_loaded_models(base_url):
            typer.echo(name)

    return app


if __name__ == "__main__":
    _build_cli()()
