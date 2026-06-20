"""
Phase-2 — Step 2b: LLM arbitrator for escalated pairs.

We re-use the Ollama dispatcher pattern from `src/extractor.py` (sequential
model loading, JSON output) but turn *on* the model's thinking mode here —
the proposal explicitly separates these two regimes:

    Phase-1 extract:    think = OFF  (load-bearing; OFF prevents empty JSON)
    Phase-2 arbitrate:  think = ON   (we want the model to reason)

Public surface:

    arbitrate(pair, *, model_name, doc_title, base_url) -> AlignedPair
    arbitrate_all(pairs, *, model_name, doc_title, base_url) -> list[AlignedPair]

The dispatcher writes back into the pair's `.conflict_label` and
`.layer2_reason` fields. If Layer-2 fails (model error, schema fail
after retries), the pair is left with `conflict_label = "escalate"` and a
diagnostic appended to `.layer2_reason` — never crash the batch.

This module **does not require Ollama to be live to be imported**. The
network call only happens inside `arbitrate()`. Unit tests can stub
`_chat_once` to exercise the logic without a model running.
"""

from __future__ import annotations
import os

import json
import logging
import re
from pathlib import Path

import requests

from .alignment import AlignedPair

logger = logging.getLogger(__name__)


# Labels the LLM is allowed to output. Anything else gets coerced to
# "escalate" with a diagnostic; we'd rather surface the mistake than
# silently mis-label.
_VALID_LABELS = {"CONTRADICTION", "GRANULARITY", "REDUNDANCY", "NO_CONFLICT"}

_DEFAULT_OLLAMA_URL = os.environ.get("OLLAMA_URL", "http://localhost:11434")
_DEFAULT_MODEL = "qwen3.5:4b"


# ---------------------------------------------------------------------------
# Prompt
# ---------------------------------------------------------------------------


def load_arbitrate_template(version: str = "v1") -> str:
    """Read prompts/arbitrate_<version>.md, stripping the leading HTML comment."""
    path = Path(__file__).resolve().parent.parent / "prompts" / f"arbitrate_{version}.md"
    text = path.read_text(encoding="utf-8")
    # Drop the leading `<!-- ... -->` block (developer notes, not for the LLM).
    text = re.sub(r"^<!--.*?-->\s*", "", text, count=1, flags=re.DOTALL)
    return text


def _render_prompt(template: str, *, doc_title: str, source_quote: str, fact_a: str, fact_b: str) -> str:
    return (
        template.replace("<<DOC_TITLE>>", doc_title)
        .replace("<<SOURCE_QUOTE>>", source_quote)
        .replace("<<FACT_A>>", fact_a)
        .replace("<<FACT_B>>", fact_b)
    )


# ---------------------------------------------------------------------------
# Ollama call
# ---------------------------------------------------------------------------


def _chat_once(
    prompt: str,
    *,
    model_name: str,
    base_url: str,
    timeout: float = 90.0,
) -> str:
    """One /api/generate call. think=OFF on purpose: with format=json the JSON
    grammar forbids qwen3.5's free-text <think> block, so thinking-mode returned
    empty responses (observed 2026-06-17: 20/20 arbitration calls empty ->
    everything stuck on 'escalate'). /no_think makes it emit the {label,reason}
    object directly; num_ctx raised so source_quote + both facts fit."""
    resp = requests.post(
        f"{base_url}/api/generate",
        json={
            "model": model_name,
            "prompt": prompt + "\n\n/no_think",
            "stream": False,
            "format": "json",
            "options": {"temperature": 0.1, "num_ctx": 4096},
        },
        timeout=timeout,
    )
    resp.raise_for_status()
    return resp.json().get("response", "")


def _parse_response(raw: str) -> tuple[str | None, str | None]:
    """Returns (label, reason). Returns (None, diagnostic) on failure."""
    if not raw or not raw.strip():
        return None, "empty_response"
    try:
        obj = json.loads(raw)
    except json.JSONDecodeError as e:
        return None, f"json_decode_error:{e.msg}"
    label = obj.get("label")
    reason = obj.get("reason", "")
    if not isinstance(label, str) or label.upper() not in _VALID_LABELS:
        return None, f"invalid_label:{label!r}"
    return label.upper(), reason if isinstance(reason, str) else ""


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def arbitrate_once(*, doc_title: str = "", source_quote: str = "",
                   fact_a: str = "", fact_b: str = "",
                   model_name: str = _DEFAULT_MODEL,
                   base_url: str = _DEFAULT_OLLAMA_URL,
                   template: str | None = None, version: str = "v1") -> dict:
    """One-shot arbitration for the conflict-prompt debug UI.

    Lenient on purpose: returns whatever `label` the model emits (no
    `_VALID_LABELS` gate), so you can prototype NEW conflict types just by
    editing the prompt. Returns {label, reason, raw}."""
    tmpl = template if (template and template.strip()) else load_arbitrate_template(version)
    prompt = _render_prompt(tmpl, doc_title=doc_title, source_quote=source_quote,
                            fact_a=fact_a, fact_b=fact_b)
    raw = _chat_once(prompt, model_name=model_name, base_url=base_url)
    label, reason = None, None
    try:
        obj = json.loads(raw)
        if isinstance(obj, dict):
            label = obj.get("label")
            reason = obj.get("reason")
    except json.JSONDecodeError:
        pass
    return {"label": label, "reason": reason, "raw": raw}


def arbitrate(
    pair: AlignedPair,
    *,
    model_name: str = _DEFAULT_MODEL,
    doc_title: str = "",
    base_url: str = _DEFAULT_OLLAMA_URL,
    template: str | None = None,
    version: str = "v1",
    chat_fn=None,            # injectable for unit tests
) -> AlignedPair:
    """Resolve a single escalated pair via LLM. Mutates and returns `pair`."""
    if pair.status != "matched":
        # Orphans don't have two facts to compare; nothing for Layer-2 here.
        return pair
    if pair.conflict_label not in ("escalate", "unlabeled"):
        # Already labelled by Layer-1 (e.g. REDUNDANCY).
        return pair

    fa, fb = pair.fact_a, pair.fact_b
    assert fa is not None and fb is not None

    template = template or load_arbitrate_template(version)
    source_quote = (fa.get("source_locator", {}) or {}).get("quote", "") or \
                   (fb.get("source_locator", {}) or {}).get("quote", "")
    prompt = _render_prompt(
        template,
        doc_title=doc_title,
        source_quote=source_quote or "(quote missing)",
        fact_a=fa.get("natural_language") or f"{fa['subject']} {fa['predicate']} {fa['object']}",
        fact_b=fb.get("natural_language") or f"{fb['subject']} {fb['predicate']} {fb['object']}",
    )

    call = chat_fn or _chat_once

    last_diagnostic = ""
    for attempt in (1, 2):  # bounded retry — Layer-2 cost adds up across pairs
        try:
            raw = call(prompt, model_name=model_name, base_url=base_url)
        except Exception as exc:  # network / server / timeout
            last_diagnostic = f"call_error_attempt_{attempt}:{exc.__class__.__name__}"
            logger.warning("Layer-2 call failed: %s", last_diagnostic)
            continue

        label, reason = _parse_response(raw)
        if label is not None:
            pair.conflict_label = label.lower()
            pair.layer2_reason = reason or f"layer2 {label}"
            return pair
        last_diagnostic = f"parse_fail_attempt_{attempt}:{reason}"

    # Both attempts failed — leave label as 'escalate' so it's visible
    pair.layer2_reason = last_diagnostic
    return pair


def arbitrate_all(
    pairs: list[AlignedPair],
    *,
    model_name: str = _DEFAULT_MODEL,
    doc_title: str = "",
    base_url: str = _DEFAULT_OLLAMA_URL,
    version: str = "v1",
    chat_fn=None,
) -> dict[str, int]:
    """Apply `arbitrate` to every escalated pair; return final label counts.

    Reads the prompt template once and shares it across calls.
    """
    template = load_arbitrate_template(version)
    counts: dict[str, int] = {}
    n_arbitrated = 0
    for p in pairs:
        if p.status == "matched" and p.conflict_label in ("escalate", "unlabeled"):
            arbitrate(
                p,
                model_name=model_name,
                doc_title=doc_title,
                base_url=base_url,
                template=template,
                chat_fn=chat_fn,
            )
            n_arbitrated += 1
        counts[p.conflict_label] = counts.get(p.conflict_label, 0) + 1
    counts["_layer2_calls"] = n_arbitrated
    return counts
