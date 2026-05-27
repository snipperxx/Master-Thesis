# Visual Analytics for Atomic Fact Annotation

MSc thesis prototype (Zilong Yan, University of Konstanz, DBvis).
Three-pane web UI for inspecting atomic facts extracted by multiple LLM
annotators, the alignment graph between them, and the conflicts that
surface when annotators disagree.

## Setup

```powershell
git clone <repo-url> MasterThesis
cd MasterThesis

python -m venv .venv
.venv\Scripts\Activate.ps1            # Linux/macOS: source .venv/bin/activate
pip install -r requirements.txt
```

Python 3.10 / 3.11. No GPU required to view the demo.

## Generate the demo doc

`data/parsed/` is gitignored, so you need to regenerate at least one
parsed document before launching the UI:

```powershell
python -m scripts.run_dry_run --n 5
```

This downloads HuggingFace `coastalcph/lex_glue` on first run (~2 GB
cache under `data/hf_cache/`) and writes 5 parsed JSONs into
`data/parsed/`. The committed demo facts target `train-000000`, which is
one of the 5.

## Launch the UI

```powershell
python -m ui.app
```

Open `http://127.0.0.1:5000/`. Pick `train-000000` from the doc selector.
The three panes (text / KG / facts table) are coupled — click any fact,
any KG edge, or any text highlight, and the other two panes follow.

That's enough to explore the prototype. Out of the box you'll see:
- 32 real Qwen3.5-4B facts + 30 + 27 synthesized facts from two
  simulated annotators (see §1 below for why),
- ~107 aligned pairs across the three annotators,
- a Cytoscape KG colored by conflict label (red contradiction, orange
  granularity, blue redundancy).

## Re-running the pipeline

Optional — only needed to regenerate data. Requires Ollama with the
three annotator models pulled.

```powershell
# 1. Install Ollama from https://ollama.com/download (auto-starts on Windows)

# 2. Pull the three annotator models (~3 GB each, Q4_K_M quantization)
ollama pull qwen3.5:4b      # required — Phase-1 main + Phase-2 Layer-2 arbitrator
ollama pull gemma3:4b       # second annotator
ollama pull phi4-mini       # third annotator

# 3. Verify
ollama list                 # all three should appear
```

Then drive the pipeline either from the UI (bottom drawer → "Background
runs" tab) or from the CLI:

```powershell
# Phase-1: LLM fact extraction (one model × one doc)
python -m src.extractor extract --model qwen3.5:4b `
    --doc data\parsed\train-000000.json --guideline v1

# Phase-2: alignment + conflict detection (drop --skip-layer2 to use LLM)
python -m scripts.run_phase2 --doc train-000000 --skip-layer2

# Tests
python -m pytest tests/
```

> On 6 GB GPU the three models cannot be co-resident — the extractor
> evicts the previous model before loading the next, so per-cell latency
> includes ~30 s of model swap. Plan for 3–5 min/cell.

## Notes

1. **Only `train-000000` has real Qwen output.** The gemma3 and
   phi4-mini annotator files are deterministic perturbations generated
   by `scripts/synthesize_facts.py` — Phase-2 needed two more annotators
   before the full 3-model extraction matrix is run. Fields tagged
   `extra.synthesized=true` mark them.
2. **EUR-Lex is fronted by AWS WAF**, so the corpus is loaded via the
   `coastalcph/lex_glue` HF dataset instead of direct fetch. Text only —
   no annex tables.
3. For full thesis context, see `PROJECT_STATE.md` and
   `doc/thesis_proposal.tex`.

Contact: yanzilongpaypal@gmail.com
