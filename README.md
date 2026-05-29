# Visual Analytics for Atomic Fact Annotation

## Quick Start

```powershell
# 1. Clone & install
git clone <repo-url> MasterThesis
cd MasterThesis
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements.txt

# 2. Download and parse 50 EUR-Lex docs from HuggingFace
python -m scripts.select_short_docs --n 50

# 3. Launch the UI
python -m ui.app
```

Open `http://127.0.0.1:5000/`. Pick any doc from the selector.

---

## Full pipeline (optional — requires Ollama)

```powershell
# Pull models (~3 GB each)
ollama pull qwen3.5:4b
ollama pull gemma3:4b
ollama pull phi4-mini

# Phase-1: extract facts (one model × one doc)
python -m src.extractor extract --model qwen3.5:4b `
    --doc data\parsed\train-000000.json --guideline v1

# Phase-2: alignment + conflict detection
python -m scripts.run_phase2 --doc train-000000 --skip-layer2

# Tests
python -m pytest tests/
```

> 6 GB GPU: models cannot co-reside. Each cell includes ~30 s model swap → plan 3–5 min/cell.

---

## What you're looking at

Three-pane web UI for inspecting atomic facts extracted by multiple LLM annotators:
- **Left** — source document text with highlighted spans
- **Center** — Cytoscape knowledge graph colored by conflict type (red = contradiction, orange = granularity, blue = redundancy)
- **Right** — facts table

All three panes are cross-linked: click any fact, KG edge, or text highlight and the others follow.

You can also trigger background extraction runs from the UI: bottom drawer → "Background runs" tab.

Out of the box (`train-000000`):
- 32 real Qwen3.5-4B facts + 30 + 27 synthesized facts from two simulated annotators
- ~107 aligned pairs across three annotators
- Conflict labels from Phase-2 two-layer pipeline

---

## Notes

- **Only `train-000000` has real Qwen output.** `gemma3` and `phi4-mini` files are deterministic perturbations from `scripts/synthesize_facts.py`. Fields tagged `extra.synthesized=true` mark them.
- **EUR-Lex is WAF-blocked** — corpus loads via `coastalcph/lex_glue` HF dataset (~2 GB cache under `data/hf_cache/`).
- For thesis context: `doc/thesis_proposal.tex`, `PROJECT_STATE.md`.

Contact: yanzilong5@gmail.com
