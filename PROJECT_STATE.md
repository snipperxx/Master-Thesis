# Project State — Master's Thesis (Zilong Yan)

**Last updated:** 2026-06-10 (pm)
**Purpose of this file:** single-source handoff document so that any future
conversation (with this AI or a different one) can pick up the project
state without re-deriving context. Update it after every substantive
working session.

---

## 1. Project Identity

- **Thesis title:** *Visual Analytics for Atomic Fact Annotation: Guideline
  Refinement through a Conflict-Aware Knowledge Graph*
- **Author:** Zilong Yan, MSc Computer Science, University of Konstanz
- **First reviewer:** Prof. Daniel A. Keim
- **Day-to-day supervisor:** Manuel Schmidt (DBvis group, PhD student)
- **Source documents (authoritative):**
  - `doc/thesis_proposal.tex`
  - `doc/thesis_milestone.tex`

### Key dates (per Konstanz ZPA)

| Event | Date |
|---|---|
| Registration deadline | **2026-05-15** |
| Bearbeitungsbeginn (official thesis start) | **≈ 2026-06-01** |
| Submission due | **≈ 2026-12-01** |

> Earlier "5月15日开始, 11月30日提交" was a misreading. ZPA assigns a 15-day
> processing buffer between registration and Bearbeitungsbeginn; the 6-month
> thesis clock starts on 2026-06-01.

### Milestone phases (with intended 2-week overlaps)

| Phase | Window | Deliverable |
|---|---|---|
| P1 — Data & Extraction | 1 Jun – 15 Jul | Multi-Model Fact DB |
| P2 — Alignment & Detection | 1 Jul – 31 Aug | Two-Layer Detection Operational |
| P3 — KG & VA Interface | 16 Aug – 15 Oct | VA Prototype with Neo4j |
| P4 — Refinement Experiment | 1 Oct – 15 Nov | v1→v2 Distribution-Shift Results |
| P5 — Final Writing | 1 Nov – 1 Dec | Submission |

> **The 2-week overlap at every junction is intentional** (next phase begins
> while current phase is wrapping up). Do not "fix" the overlap by trimming.

### Hardware constraint

- **GPU:** RTX 3060M, **6 GB VRAM** (laptop).
- Implication: 3 × 4B-class models cannot be co-resident even at FP16.
  Plan is to use **Q4_K_M** quantizations (each ≈ 2.5–3 GB) and run
  **sequentially** via `ollama stop / ollama run`. The dispatcher must be
  abstracted so Phase-2 LLM arbitration doesn't fight Phase-1 extraction
  for the GPU.

---

## 2. Decisions Already Locked In

These have been thought through; do not re-litigate without new evidence.

1. **Corpus = EUR-Lex EN regulatory documents.** Chosen for fact density
   and for the abundance of *entity-boundary* and *conditional-clause*
   ambiguity that the thesis specifically targets. Risk: 4B models may
   struggle with legal vocabulary — mitigated by §3 dry-run gate.
2. **Corpus loading via HuggingFace `coastalcph/lex_glue` (eurlex subset)
   — NOT direct HTTP fetch.** Reason: `eur-lex.europa.eu` is fronted by
   AWS WAF (returns a JS challenge page); `publications.europa.eu`
   (CELLAR) returned HTTP 400 on our queries. Rather than keep debugging
   URL/CELEX-validity, we use the 57k pre-cleaned English documents on
   HF. Trade-off: plain text only — no annex table structure. We accept
   this for now because Phase-1's main question (can 4B models do atomic
   fact extraction on legal prose?) is testable without tables. The HTML
   fetcher and `table_linearize.py` are kept in the codebase for future
   use when annex tables become necessary (Phase 4 stress test).
3. **Source-agnostic JSON schema** (in `src/schema.py`) using a
   discriminated union over `source_locator` (`ProseLocator` |
   `TableLocator`). Same schema accepts LLM facts and human-annotated
   facts — no separate ingestion path.
4. **Annex tables = first-class citizens.** They are the cleanest
   testbed for *granularity disagreement* (does a row become 1 molecular
   fact or N atomic facts?). Pipeline linearizes each row to a
   self-contained sentence using column headers and feeds those to the
   LLM, with a `TableLocator` carrying back the cell coordinates.
5. **No LLM in Phase-1 dry run.** The first goal is to verify
   structural parsing on real EUR-Lex HTML before burning GPU time.

---

## 3. Pre-Thesis Window (now → 2026-06-01)

The clock isn't running yet. Do not write thesis prose. Use this window
for engineering scaffolding so 6/1 starts at full speed.

### Hard checkpoint: dry run before committing to EUR-Lex

Before scaling to 50–80 documents in P1, run the existing pipeline
against the 5 candidate CELEXs in `data/celex_dry_run.txt`. Inspect:

- Are 4B models able to produce *schema-valid* JSON facts on legal
  prose? (Target: ≥ 90% of model outputs parse cleanly.)
- Are the resulting facts something a human reader can call "atomic"
  without specialized legal training?
- Do the three models (Qwen3.5-4B / Gemma3-4B / Phi4-mini) produce
  disagreements with **interpretable patterns** (e.g. "Phi splits
  conditional clauses, Qwen doesn't"), or is the disagreement just
  noise?

If any of these fail, fall back in this order:
1. EUR-Lex subset (recitals + short directives only, skip annex-heavy
   regulations).
2. Wikipedia "current events" / legislative summaries (lower difficulty).
3. CNN/DailyMail or XSum news (last resort — dilutes the research signal
   because guideline ambiguity is rarer in news prose).

### Pre-6/1 checklist

- [ ] **2026-05-15** — Submit ZPA Antrag. Do **not** write `15.05.2026`
      as Bearbeitungsbeginn; either leave blank or write `01.06.2026`.
- [ ] Set up dev environment: Python 3.11 venv, `pip install -r
      requirements.txt`, Docker for Neo4j, Ollama with Q4_K_M pulls
      of the 3 models.
- [x] Run `python -m scripts.run_dry_run` (HF mode default, downloads
      `coastalcph/lex_glue` eurlex on first run). 4 of 5 sampled docs
      parsed cleanly (2 citations / 5–8 recitals / 2–3 articles each);
      one pre-2000 EEC act had 0 recitals (different recital format).
      After Gemini-suggested fixes, multi-line title and clean
      `enacting_text` are both in source; re-verify after the next
      `pip install` / pycache clean.
- [x] Write the **extraction prompt** `prompts/extract_v1.md`
      (2026-05-22). Markdown template with `<<DOC_TITLE>>`,
      `<<SECTION_PATH>>`, `<<SECTION_TEXT>>` sentinels and a 9-rule
      editable `<guideline>` block — the v1 baseline for the Phase-4
      refinement experiment.
- [x] Wire **Ollama dispatcher** — `src/extractor.py` (2026-05-22).
      `extract(model_name, parsed_doc) -> list[AtomicFact]` with
      sequential model loading via Ollama `/api/ps` + `keep_alive=0`
      eviction, JSON-schema retry loop (×2), RapidFuzz offset recovery,
      and a CLI: `python -m src.extractor extract --model … --doc …`.
      First end-to-end run on 2026-05-22 produced 32 schema-valid
      facts on `train-000000` (qwen3.5:4b) — see §4 "First extraction
      run" below.
- [ ] **Pull the other two models**: `ollama pull gemma3:4b` and
      `ollama pull phi4-mini`. (qwen3.5:4b already on disk.)
- [ ] **Write `scripts/run_extraction.py`** — batch driver that takes
      `--models a,b,c --docs data/parsed/*.json` and produces the full
      `data/facts/<model>/<doc>.json` matrix. The current CLI only
      handles one (model, doc) at a time.
- [ ] **Scale parsed-doc set to 50–80** by re-running `run_dry_run`
      with `--n 80`. Only 5 docs are parsed today.
- [ ] **Spot-check QA**: random-sample ≈ 50 facts across the matrix,
      record failure modes; this feeds the Phase-4 v2 guideline draft.
- [ ] **Kickoff sync with Manuel** before 6/1. Five things to confirm
      (see §7): doc selection criteria, JSON schema fields, pilot
      evidence on 4B-on-legal-text, late-July exam crunch scheduling,
      annex-table granularity policy. Bring the 32-fact `train-000000`
      output as a concrete anchor for the conversation.

---

## 4. What's Built So Far

```
MasterThesis/
├── PROJECT_STATE.md           ← this file
├── requirements.txt
├── .gitignore
├── doc/
│   ├── thesis_proposal.tex    (authoritative — do not paraphrase)
│   ├── thesis_milestone.tex   (authoritative)
│   └── 毕设题目.pdf
├── prompts/
│   └── extract_v1.md          ← editable <guideline> block + JSON contract;
│                                v1 baseline for the Phase-4 experiment
├── src/
│   ├── __init__.py
│   ├── schema.py              ← AtomicFact + ParsedDocument Pydantic models
│   ├── eurlex_dataset.py      ← HF lex_glue loader (PRIMARY corpus source)
│   ├── eurlex_fetch.py        ← HTTP fetch (DORMANT — eur-lex blocked by WAF)
│   ├── eurlex_parse.py        ← parse_document() HTML + parse_plain_text() HF
│   ├── table_linearize.py     ← annex row → sentence (DORMANT — needs HTML)
│   └── extractor.py           ← Ollama dispatcher: extract(model, doc) →
│                                list[AtomicFact]; CLI subcommands
│                                {extract, unload, ps}
├── scripts/
│   ├── __init__.py
│   └── run_dry_run.py         ← end-to-end CLI (no LLM)
└── data/
    ├── celex_dry_run.txt      ← 5 candidate CELEXs
    ├── parsed/                ← ParsedDocument JSON per doc (5 today)
    └── facts/<model>/<doc>.json  ← Phase-1 LLM output
```

### Module responsibilities

| Module | Role | Stable contract |
|---|---|---|
| `schema.AtomicFact` | universal fact representation | union locator: `ProseLocator` (char offsets) ∨ `TableLocator` (cell coords) |
| `schema.ParsedDocument` | structural decomposition of one EUR-Lex act | title / citations / recitals / articles / annexes / concluding |
| `eurlex_fetch.fetch_celex` | get HTML for a CELEX | caches at `data/raw_html/`; polite 1s rate limit |
| `eurlex_parse.parse_document` | HTML → `ParsedDocument` | defensive: degrades gracefully on unknown structure |
| `table_linearize.linearize_table` | row → sentence | uses column headers as labels; produces `LinearizedRow(sentence, locator)` |
| `scripts.run_dry_run` | CLI: CELEX list → JSON | writes `data/parsed/<celex>.json`; prints rich summary table |
| `extractor.extract` | `(model_name, parsed_doc) → list[AtomicFact]` | sequential VRAM management; per-section chunking (each recital + each article); `format="json"` + `think=False` for Qwen3; schema retry loop ×2; RapidFuzz offset recovery into `preamble_text` / `enacting_text` |
| `extractor.load_prompt_template` | reads `prompts/extract_<version>.md` | sentinel-based substitution so legal text with literal `{`/`}` doesn't break |
| `extractor` CLI | `python -m src.extractor {extract,unload,ps}` | writes `data/facts/<model_safe>/<doc_id>.json` |

### Verified

- HTML parser (`parse_document`) tested against a synthetic HTML fixture
  mimicking EUR-Lex structure — all sections recovered correctly.
- Plain-text parser (`parse_plain_text`) tested against:
  - A synthetic HF-style fixture — passed.
  - One real HF document (`train-000000`, Netherlands deficit Council
    Decision 2005/729/EC) — 2/2 citations, 6/6 recitals, 3/3 articles,
    concluding formula all extracted correctly. Two minor cosmetic
    issues were fixed in a follow-up edit (multi-line title concatenation,
    stray colon in `enacting_text`). See "Known parser fragilities" for
    the open issue with pre-2000 EEC-era documents.
- Live EUR-Lex HTTP fetch is **blocked**: eur-lex.europa.eu serves an
  AWS WAF JS challenge to non-browser clients; publications.europa.eu
  (CELLAR) returns HTTP 400. HF dataset path bypasses both.

### First extraction run (2026-05-22)

End-to-end Phase-1 ran for the first time. Command:

```powershell
python -m src.extractor extract --model qwen3.5:4b `
    --doc data\parsed\train-000000.json --guideline v1
```

Output: `data/facts/qwen3.5_4b/train-000000.json`, **32 facts**,
≈ 5 min on RTX 3060M. All anchor scores ≥ 99 (offsets trustworthy).
Per-section breakdown: recital[0]=3, [1]=3, [2]=1, [3]=2, [4]=18,
[5]=0, article_1=3, article_2=1, article_3=1.

**Four v1-guideline violation patterns surfaced** — these are
deliverable signal, not bugs. They become the Phase-4 v2 revision
targets:

1. **Unresolved demonstratives** — e.g. `subject: "This Decision"`,
   `object: "this threshold"`, `object: "by the Recommendation"`.
   Violates v1 §2 (decontextualization). Breaks KG entity resolution.
2. **Subject/object swapped in short imperative articles** — Article 2
   (`"Decision 2005/136/EC is hereby abrogated."`) was emitted with
   `object: "null"` (literal string). Correct SVO is *(this Decision,
   abrogates, Decision 2005/136/EC)*.
3. **Conditional clauses split into N facts** — Article 1's single
   sentence was split into 3 facts sharing subject/predicate with
   varying object. Directly violates v1 §6 ("keep conditional clauses
   as one fact, put the condition in `source_quote`"). Phase-2
   Layer-2 should align those 3 to one cluster and label GRANULARITY.
4. **Surface-form variants of the same entity** — `"the Council"` /
   `"the Commission"` / `"Commission services"`; and `"general
   government deficit"` / `"general government balance"` / `"the
   deficit"` / `"cyclically adjusted deficit"`. Phase-3 SBERT entity
   resolution will merge these with the editable threshold.

**Two operational quirks worth not re-discovering:**

* **Qwen3 thinking eats JSON.** Qwen3.5 (and any hybrid-reasoning
  model) defaults to thinking mode and returns an *empty* `response`
  field under `format="json"`. The dispatcher sets `think=False` and
  appends `/no_think` to the prompt in `render_prompt`. **Do not
  undo this for Phase 1.** Layer-2 (Phase 2) should opt back into
  thinking by calling `render_prompt(..., suppress_thinking=False)`
  and dropping `"think": False` from the payload.
* **`ollama --version` printing "could not connect" is harmless.**
  The CLI warning is independent of the HTTP API used by the
  dispatcher; the Python client at `http://localhost:11434` works
  regardless.

### Current code state — Gemini-reviewed parser fixes (2026-05-17)

After the initial real-document smoke test surfaced two cosmetic issues,
the following changes are now in `src/eurlex_parse.py`:

1. **`_ENACTING_MARKER_RE`** now ends with `[\s:]*`, swallowing the
   trailing colon after "HAS ADOPTED THIS REGULATION:" at the regex
   layer. This is cleaner than post-hoc `.lstrip()`.
2. **`_TITLE_END_RE`** uses a simplified `(?:THE\s+(?:COUNCIL|EUROPEAN|PARLIAMENT)|Having\s+regard\s+to)`
   pattern with `IGNORECASE`, and `parse_plain_text` collects all
   non-blank lines before that match as the multi-line title joined
   by " — ".

**Sandbox limitation**: in this session, the Linux sandbox could not
delete `__pycache__/*.pyc` files (Windows-mounted filesystem refuses
the unlink), so the import-based verification kept hitting stale
bytecode. The source on disk is correct (verified by reading the bytes
back), and exec()-based loading binds `_TITLE_END_RE` properly. On the
user's Windows machine, Python's mtime-based `.pyc` invalidation should
work normally — but if it doesn't, manually clean the cache before
re-running:

```powershell
Get-ChildItem -Recurse -Force -Filter __pycache__ | Remove-Item -Recurse -Force
```

Then re-run `python -m scripts.run_dry_run` and check that `train-000000.json`
shows a multi-line title (containing "of 7 June 2005" and "(2005/729/EC)")
and an `enacting_text` that starts with "Article" not ":".

### Known parser fragilities

These are likely to surface on first real document:

1. **Article heading detection** assumes CSS class `oj-ti-art` or text
   match `^Article \d+`. Older acts may use `eli-subdivision` divs or
   different class names.
2. **Preamble→Enacting boundary** uses the literal "HAS ADOPTED THIS
   REGULATION/DECISION/DIRECTIVE/RECOMMENDATION" marker. Other act
   types may use different wording.
3. **Annex header detection** uses `^ANNEX[ I-X0-9]*`. Multi-language
   leakage or non-Roman numerals may slip through.
4. **Table header heuristic** assumes short cells without long numbers.
   May misclassify the first data row as a header on tables without
   `<thead>`.

When debugging: inspect the per-document JSON in `data/parsed/`. Compare
counts against the document on `eur-lex.europa.eu`. The parser is
intentionally permissive — bad parsing produces wrong section assignments,
not crashes, so verify by reading.

---

## 5. Roadmap by Phase

### Phase 1 — Data Pipeline & Extraction (1 Jun – 15 Jul)

**Done already (pre-6/1 work):** HTML fetch, structural parser, table
linearization, JSON schema, `prompts/extract_v1.md`, `src/extractor.py`,
RapidFuzz offset recovery, first end-to-end run (qwen3.5:4b on 1 doc,
2026-05-22).

**Still to build:**

- Batch driver `scripts/run_extraction.py` that runs the full
  `<models> × <docs>` matrix in one shot (dispatcher today is one-shot).
- Pull `gemma3:4b` and `phi4-mini`; re-run on `train-000000` for first
  3-model comparison; verify VRAM eviction works between models.
- Scale parsed-doc set from 5 → 50–80 via `run_dry_run --n 80`.
- Spot-check QA pass on ~50 random facts across the matrix.
- **Phase-1 deliverable:** `data/facts/<model>/<celex>.json` files
  containing schema-valid facts with verified character offsets, plus
  a short failure-mode log to seed the Phase-4 v2 guideline draft.

### Phase 2 — Alignment & Two-Layer Detection (1 Jul – 31 Aug)

- SBERT embedding of each fact's natural-language form.
- Hungarian one-to-one alignment between models, default similarity
  threshold 0.78 (tunable later).
- Layer-1 rule filter: cosine > 0.95 + high object-field char overlap
  → `REDUNDANCY`. Antonym trap → escalate.
- Layer-2 LLM arbitrator: Qwen3.5-4B with a 4-shot prompt returning
  one of {`CONTRADICTION`, `GRANULARITY`, `REDUNDANCY`, `NO_CONFLICT`}.
- **Thesis writing starts 1 Jul** — Introduction + Related Work drafts.
- **Phase-2 deliverable:** aligned-pair table with conflict labels.

**Built ahead of schedule (2026-05-24):** scaffolding for the whole
Phase-2 + Phase-3 stack is in place against **synthetic** multi-annotator
data derived from `train-000000`. The real 3-model × 50-doc extraction
matrix still has to run before any of these numbers are publishable.
Modules added:

| File | Role |
|---|---|
| `scripts/synthesize_facts.py` | Generates `gemma3-4b-sim` (30 facts) and `phi4-mini-sim` (27 facts) from the real qwen output by perturbing surface form, splitting facts, and bumping numbers — gives Phase-2 a known-truth regression set with all 4 ConflictLabels. Fully deterministic (seed=42). Every synthesized fact is tagged `extra.synthesized=True`. |
| `src/alignment.py` | SBERT (all-MiniLM-L6-v2) + scipy Hungarian. Auto-falls-back to sklearn char-trigram TF-IDF when torch is unavailable (sandbox safety net; user's Windows venv runs the real SBERT path). |
| `src/conflict_layer1.py` | Configurable rule filter: REDUNDANCY (cosine ≥ 0.95 + subj+obj char-trigram Jaccard ≥ 0.75); numeric mismatch & polarity asymmetry → `escalate`. |
| `src/conflict_layer2.py` | Ollama dispatcher for Layer-2 arbitration. **`think=True`** here (proposal-mandated split with Phase-1 `think=False`). Bounded retry loop, schema validation, never crashes the batch. |
| `prompts/arbitrate_v1.md` | The 4-label prompt template; sentinel substitution like extract_v1. |
| `src/kg_build.py` | Phase-2 output → Cytoscape graph. Edge label = strongest conflict on any pair that rides the edge; node colour propagated from edges. |
| `scripts/run_phase2.py` | Batch driver. `--skip-layer2` swaps in a deterministic stub for sandbox / CI runs. |
| `ui/app.py` + `ui/templates/index.html` + `ui/static/{app.css,app.js}` | Flask backend + single-page UI with three coupled panes: text (left), KG (centre), facts table (right). |

**First end-to-end run** (synthetic, sandbox, TF-IDF backend):
qwen3.5:4b (32) + gemma3-4b-sim (30) + phi4-mini-sim (27) → 107 aligned
pairs → Layer-1 collapsed 35 trivial REDUNDANCYs → Layer-2 (stub)
labelled 31 GRANULARITY + 5 CONTRADICTION → KG has 57 nodes / 59 edges
at merge_threshold=0.78.

### Poster-alignment + closed-loop build-out (2026-06-10)

Gap-closing session against Manuel's poster/paper (doc/毕设题目.pdf) and the
proposal. All additions are **append-only**: new modules + new JS files;
existing code got only parameter-level edits (verified via git diff).

**The critical fix — versioned experiment artifacts.** Until now a v2 re-run
*overwrote* the v1 facts (`data/facts/<model>/<doc>.json`) and conflicts
(`data/conflicts/<doc>.json`), and `distribution_shift` looked for
`<doc>__v2_*.json` files that nothing ever wrote → "Compare v1↔v2" was dead.
Now:

- non-v1 Phase-1 output goes to `data/facts__<version>/<model>/<doc>.json`
  (`reextract_worker.facts_root_for_version`); v1 baseline is never clobbered.
- `run_phase2.run(..., out_suffix=<version>)` writes
  `data/conflicts/<doc>__<version>.json`.
- new Job kind **`pipeline`** (`enqueue_pipeline`) chains Phase-1 → Phase-2
  in one job; `POST /api/run_pipeline` (UI: **Experiment** drawer tab).
- `?variant=<version>` on `/api/{facts,graph,pairs,coverage,similarity_matrix,iaa}`
  + top-bar version dropdown (via `ui/static/variant_shim.js` fetch wrapper —
  app.js untouched) lets the three panes show any experiment version.
- `GET /api/distribution_shift_agg?v2=<v>` aggregates the shift corpus-wide
  (the thesis metric); per-doc endpoint unchanged.

**Poster views (center pane is now tabbed: Graph | Multiples | Heatmap | Stats**,
`ui/static/analysis.js`):

- *Heatmap* — fact×fact cosine matrix per annotator pair, Hungarian matches
  outlined in conflict colour, cell click → pair inspector → "review →"
  cross-link. Backend `src/annotator_compare.similarity_matrix` +
  `GET /api/similarity_matrix/<doc>?a=&b=`. Shows a ⚠ chip when the TF-IDF
  fallback (no torch) produced the numbers.
- *Stats* — fact-count histogram per annotator (poster Fig. 2 right),
  per-section count table with spread highlighting, and an IAA table
  (Jaccard |matched|/|union|, poster metric) from `GET /api/iaa/<doc>`.
- *Multiples* — small-multiple per-annotator KGs with a SHARED layout
  (headless concentric on the union graph → preset positions per panel,
  non-owned entities ghosted) — poster Fig. 1. Node tap syncs selection
  across panels and the main graph.

**Conflict review queue (drawer tab "Review"**, `ui/static/workflow.js`,
`src/review_store.py`): per-pair side-by-side inspection with rule-attribution
checkboxes (rules parsed from the `### N.` headings inside the guideline's
`<guideline>` block), note, agree/relabel/dismiss, auto-advance, pre-populated
web-search verify link (proposal §Phase-3). Persists to
`data/reviews/<doc>.json`; `GET /api/review_summary` tallies conflicts per
rule across docs → rendered as the **evidence panel** in the Experiment tab
("§6 implicated 12× — 9 granularity"), which is the empirical input for
authoring v2.

**Experiment tab** = the closed loop in one click: pick guideline → models →
docs → Run; job chains P1→P2; on completion the v1↔vX comparison
(corpus aggregate + current doc) opens automatically and the version
dropdown gains the new variant. "Skip extraction" re-uses on-disk facts for
fast Phase-2-only iterations (threshold tuning etc.).

Tests: 39/39 passing (`tests/test_annotator_compare.py`,
`tests/test_review_store.py` added). Full pipeline-job integration verified
in-sandbox with skip_extract + stub Layer-2.

**Sandbox cleanup caveat (2026-06-10):** the Linux sandbox cannot delete
files on the Windows mount, so smoke-test artifacts remain — they make the
new UI demoable but are SYNTHETIC (v2 facts = copy of v1):
`data/facts__v2/`, `data/conflicts/train-000000__v2.json`,
`data/graphs/train-000000__v2.json`, `data/reviews/train-000000.json`
(one fake review aaa|bbb). Delete in PowerShell before real Phase-4 runs,
and clear `__pycache__` once (run_phase2/reextract_worker/app changed):
`Get-ChildItem -Recurse -Force -Filter __pycache__ | Remove-Item -Recurse -Force`

### Live walkthrough session (2026-06-10, afternoon)

Agent-driven in-browser test of the running app; 7 further fixes. Full
issue→fix→rationale record in **`doc/ui_iteration_log.md`** (written for the
thesis design chapter — keep updating it every session).

Highlights: fixed `doc_subset`→`doc_ids` param mismatch (Extract-here would
have launched Phase-1 on all 51 docs); per-fact edge labels now always
resolved (top-bar counts were permanently zero); Ollama transient-500 retry
in `extractor._post`; `run_phase2` refuses <2 annotators (a failed cell
previously fabricated a zero-conflict v2 file); per-section progress
(`job.progress.detail`); compare modal warns when v1/v2 annotator sets
differ; Compare button survives reload.

**First real closed-loop run** (v2 × qwen+gemma × train-000000, stub L2):
qwen 25 facts / gemma 14 → 30 pairs vs v1's 106. ⚠ NOT a valid guideline
effect — v1 baseline still contains 2 synthetic annotators (3-annotator set
vs 2). **Before Phase-4: rebuild the v1 baseline with the same real model
set.** Extraction nondeterminism ≈ ±1 pair across two identical v2 runs.

Known operational quirks: Flask --debug reloader unreliable for src/ edits
on this volume (restart manually after backend changes; JS/CSS always
fresh); restarting drops the in-memory job queue (don't restart mid-run).

### Phase 3 — KG & VA Interface (16 Aug – 15 Oct)

The Phase-3 UI is **already prototyped** as of 2026-05-24, against the
synthetic Phase-2 output above. Done:

- Single-page Flask app served at `http://127.0.0.1:5000/` via
  `python -m ui.app`.
- Three coupled views (text / Cytoscape graph / facts table) with full
  cross-view selection: click any row, mark, or graph edge → all three
  panes highlight the same fact.
- Top-bar controls: doc selector, annotator chips (toggle to filter),
  conflict-label dropdown, **merge-threshold slider** that hot-reloads
  the graph from the server (slider debounced 250 ms).
- Per-fact verify ✓/✗ buttons persisting to
  `data/verifications/<doc>.json`.
- Edge colouring by conflict severity (CONTRADICTION > GRANULARITY >
  REDUNDANCY > unlabeled), node ring colour propagated from incident
  edges.
- Bottom drawer: guideline live-edit panel — POST endpoint exists but
  returns 501 until Phase-4 wires the re-extraction worker.

**Still to wire for the real Phase-3 deliverable** (not blockers now):

- Replace synthetic data with the real 3×50 matrix (Phase-1 batch
  driver already exists at `scripts/run_extraction.py` — typer-based,
  manifest-resumable, model-outer loop).
- Neo4j backend (currently we serve graph JSON straight out of `data/graphs/`).
- Real cardinality-constraint editor (Phase-3 §multi-hop patching);
  current verify is fact-level only.

### Background runs from UI (2026-05-25, late)

The whole pipeline is now driven from the browser. No more switching to a
PowerShell tab to kick off long runs.

| Endpoint | Triggered by | Job kind |
|---|---|---|
| `POST /api/run_phase1` | "Run Phase-1" button in the Background-runs drawer tab | `reextract` — uses existing guideline_version (no fresh body) |
| `POST /api/run_phase2` | "Run Phase-2" button | `phase2` — calls `scripts.run_phase2.run()` per doc |
| `POST /api/jobs/<id>/cancel` | × button on queued jobs in the Live jobs panel | (soft cancel only) |

The worker (`src.reextract_worker`) now dispatches on `Job.kind`:
- `reextract` runs `extractor.run_doc()` per (model, doc) cell, as before.
- `phase2` loops doc_ids, calls `scripts.run_phase2.run()` per doc, accumulates label counts in `Job.results`.

UI changes:
- **Live jobs panel** sits permanently under the top bar, toggled with the "Jobs ▾" button. It polls `/api/jobs` every 2s while jobs are in-flight, 10s when idle. Shows kind chip / label / progress bar / status badge / cancel button. Auto-opens when a new job appears.
- **Background runs** is now the default drawer tab. Two fieldsets: Phase-1 (models, guideline, optional doc subset) and Phase-2 (skip-layer2, layer2-model, align-threshold). Both have a confirm dialog with estimated cell count.

### Phase 5 build-out (2026-05-25, evening)

Four further features added on top of the Phase-3+4 stack so the prototype
becomes self-contained (no longer requires a pre-existing EUR-Lex doc set
to demo):

| Feature | Backend | UI |
|---|---|---|
| **Upload arbitrary text** — paste raw text, choose paragraph/sentence split, optionally trigger Phase-1 extraction in one click | `POST /api/upload_text` calling `src.text_ingest.build_parsed_doc()` → writes `data/parsed/user-<hash>.json` | `+ Text` button next to doc selector → modal with title/textarea/split mode/extract toggle |
| **Selective span re-extraction** — highlight any text span in the left pane, pick model+guideline, re-extract only that span; results merge into the existing facts file (old facts in that span are dropped) | `POST /api/reextract_span {doc_id, char_start, char_end, model, guideline_version}` builds a one-section `ParsedDocument`, calls `extract()`, merges into `data/facts/<model>/<doc>.json` | `mouseup` over `#doc-body` shows a floating menu near the selection |
| **Guideline manager** — list / view / edit / save as new version every `prompts/extract_*.md`, with sentinel validation on save | `GET /api/guidelines`, `GET /api/guidelines/<v>`, `PUT /api/guidelines/<v>` (rejects bodies without all three sentinels) | New drawer tab "Guidelines" with select / load / save-as |
| **Run matrix** — interactive (doc × model × guideline) checkbox grid; one job per checked cell, status overlay shows fact_count on cells with prior output | `POST /api/run_matrix {cells:[...]}` enqueues one job per cell; `GET /api/run_matrix/status` returns existing fact counts per (doc, model) | New drawer tab "Run matrix" with sticky-header table |

The whole stack now has **29 pytest tests passing** (added `tests/test_text_ingest.py`).

### Phase 3+4 build-out (2026-05-25)

**Algorithms shipped:**

| File | Purpose |
|---|---|
| `src/coverage.py` | Per-annotator coverage: sections_hit_frac, char_coverage_frac, mean_fact_chars. Pure (no I/O), aggregator across docs included. |
| `src/distribution_shift.py` | Compare two Phase-2 outputs by label and Layer-1 filter rate. Bar-chart-friendly JSON. |
| `src/reextract_worker.py` | Single-thread, in-memory job queue for Phase-4 re-extraction. Persists guideline-text to `prompts/extract_<job_id>.md`, calls `extractor.run_doc` per (model, doc), updates `_JOBS[id]` in place. |
| `scripts/run_phase2.py --all` | Batch-mode Phase-2: discovers every doc_id with ≥ 2 annotators, runs the pipeline per doc, aggregates label counts across the batch. |
| `prompts/extract_v2.md` | v2 baseline guideline addressing the 4 documented v1 violation patterns (demonstratives, subject/object swap in imperative articles, conditional-clause splits, entity surface variants). |
| `tests/` | pytest covering alignment, layer1, kg_build, coverage. 24/24 passing under TF-IDF backend in the sandbox; SBERT path covered structurally. Run: `python -m pytest tests/ --basetemp=/tmp/pytest_tmp`. |

**UI shipped:**

| Endpoint / feature | Notes |
|---|---|
| `GET /api/coverage/<doc>` | Drives the top-bar coverage chips. |
| `GET /api/distribution_shift/<doc>` | Backs the "Compare v1↔v2" modal. Returns a `note` gracefully when no v2 file exists yet. |
| `POST /api/guideline` | **Now returns 202** + job_id (previously 501 stub). Body: `{guideline_text, models?, doc_paths?}`. |
| `GET /api/jobs[/<id>]` | List + poll re-extraction jobs. |
| Pair-detail modal | Click any KG edge → shows every aligned pair on that edge (A/B side-by-side, cos, Layer-1/2 reason). |
| Compare modal | Top-bar "Compare v1↔v2" → inline two-tone bar chart per label + Δ% + Layer-1 filter rate change. |
| Coverage chips row | Per-annotator section% / char% bars under the top controls. |
| Layout switcher | cose / concentric / breadthfirst / grid. |
| Search box | Substring match against subject/predicate/object — narrows all three panes in real time. |
| Hot keys | `j` / `k` cycle through visible facts; `Esc` closes modals. |
| Multi-layer overlapping highlights | Span depth ≥ 2 gets a colored inset underline so co-located facts are still distinguishable. |
| CSV + JSON export | Visible facts, both formats; CSV includes verification status. |

### Phase 3 — KG & VA Interface (16 Aug – 15 Oct)

- Triples (S, P, O) into Neo4j with provenance metadata.
- Entity resolution via SBERT clustering with editable threshold.
- Cytoscape.js front-end on Flask back-end with three-level node
  colouring, conflict-cluster filtering, in-tool fact verification,
  cardinality-constraint editor (multi-hop patching), and a guideline
  live-edit panel that re-runs extraction.
- **Thesis writing:** Methodology + System Architecture chapters.

### Phase 4 — Refinement Experiment (1 Oct – 15 Nov)

- Run baseline guideline `v1` over the full corpus.
- Author guideline `v2` based on KG-surfaced disagreement patterns.
- Re-run pipeline; compute Conflict Type Distribution Shift, Total
  Conflict Count Reduction, Layer-1 Filter Rate Change.
- Ingest a small set of pre-annotated human facts for qualitative
  human↔model conflict demonstration.
- **Thesis writing:** VA Design + Evaluation setup chapters.

### Phase 5 — Submission (1 Nov – 1 Dec)

- Synthesize drafts into final manuscript; write Evaluation results
  + Conclusion; finalize figures; LaTeX polish.

---

## 6. Working Conventions

These are how Zilong has asked to collaborate; do not deviate without
checking with him.

- **Language:** explanations in Chinese; code/identifiers/comments in
  English.
- **Authoritative source:** when in doubt about thesis content, re-read
  `doc/thesis_proposal.tex` and `doc/thesis_milestone.tex`. Do not
  paraphrase from memory.
- **Phase overlap is intentional** — flagged twice already.
- **Scope discipline:** if a suggestion goes beyond what the proposal
  scopes (e.g. multi-hop reasoning beyond 1-hop pair comparison), call
  it out as out-of-scope and propose a Phase-3 *interactive constraint*
  workaround instead.
- **No premature thesis prose** before P2 starts (1 Jul). The reason is
  that Phase-1 produces first-hand observations about real fact-extraction
  failures that should inform the Motivation/Related Work framing.

---

## 7. Open Questions for Manuel (Kickoff Agenda)

1. **Document selection criteria for EUR-Lex** — by document length?
   By legal sub-domain? By presence/absence of annex tables? Any
   criteria his group has already validated?
2. **JSON schema fields** — does the field set in `src/schema.py` align
   with how his group has structured human annotations elsewhere?
   Specifically: `subject/predicate/object` triple form vs.
   natural-language only.
3. **Pilot evidence on 4B models for legal text** — has anyone in the
   group benchmarked small LMs on EUR-Lex specifically? Would inform
   how seriously to weight the dry-run gate (§3).
4. **Late-July exam crunch (≈ 4 exams + 2 weeks cramming)** falls in
   P2 mid-window. Schedule cascade work around it.
5. **Annex-table strategy** — confirm that linearizing rows with column
   headers and feeding them as separate units is acceptable as the
   "atomic" unit, or whether he expects whole-table reasoning.

---

## 8. How to Run What's Built

```bash
# One-time setup
python -m venv .venv
.venv\Scripts\activate              # Windows PowerShell: .venv\Scripts\Activate.ps1
pip install -r requirements.txt

# Dry run on the 5 seed CELEXs
python -m scripts.run_dry_run

# Outputs:
#   data/raw_html/<celex>.<hash>.html   ← cached HTML
#   data/parsed/<celex>.json            ← structured ParsedDocument + linearized rows
# Console: rich summary table (citations / recitals / articles / annexes / tables / rows)
```

When something looks off in the parsed JSON, open the corresponding raw
HTML and locate the offending section. The parser's defensive design
will produce wrong section assignments rather than crashes, so always
sanity-check counts against the live document on `eur-lex.europa.eu`.

---

## 9. Handoff Pointer — for the next conversation window

If you're a new conversation (or a different AI) reading this:

1. **Read these three files first**, in order:
   - `PROJECT_STATE.md` (this file) — full context
   - `doc/thesis_proposal.tex` — authoritative thesis design
   - `doc/thesis_milestone.tex` — authoritative timeline

2. **Current state at handoff (2026-05-24):**
   - **Phase-1** pipeline operational; first real LLM run done
     (qwen3.5:4b × train-000000 → 32 facts). 50 docs short-listed in
     `data/doc_lists/phase1_short.json`. The 3-model × 50-doc matrix
     has NOT been run yet — only train-000000 × qwen is real LLM output.
   - **Phase-2 scaffolding is done** against *synthetic* multi-annotator
     facts: `scripts/synthesize_facts.py` derives `gemma3-4b-sim` and
     `phi4-mini-sim` from the qwen output with seeded perturbations.
     Real outputs from `gemma3:4b` and `phi4-mini` will drop into the
     same `data/facts/<model>/<doc>.json` slots and the rest of the
     pipeline doesn't care.
   - **Phase-3 UI prototype is done.** Flask + Cytoscape.js single-page
     app at `http://127.0.0.1:5000`, three coupled views, threshold
     slider, verify buttons. See §5 Phase-3 entry for the still-to-do
     list (Neo4j, real re-extraction worker, cardinality editor).
   - **Next concrete actions (in order):**
     1. `ollama pull gemma3:4b` + `ollama pull phi4-mini`.
     2. Write `scripts/run_extraction.py` (batch driver for Phase-1).
     3. Scale parsed-doc set to 50–80 (`run_dry_run --n 80`).
     4. Run the full 3 × 50–80 matrix → replaces synthetic data.
     5. Re-run `scripts/run_phase2.py` (drop `--skip-layer2` once
        Ollama+Qwen is loaded) for each doc; verify Layer-2 labels are
        sane on real model output.
     6. Kickoff sync with Manuel before 6/1.

3. **Things that wasted time this session — don't repeat:**
   - Trying to fetch EUR-Lex via `eur-lex.europa.eu` HTTP → blocked by
     AWS WAF (JS challenge). CELLAR via `publications.europa.eu` also
     returned 400. The HF dataset path bypasses both and is the
     decision of record (§2 entry 2).
   - Hand-guessing CELEX numbers for the seed list. They were all
     either invalid or behind WAF, hence the pivot.
   - In the Linux sandbox, `.pyc` files in `__pycache__` on the
     Windows-mounted volume cannot be deleted, causing stale-bytecode
     import errors during smoke-testing. If you need to verify a
     parse_plain_text edit in sandbox, use `exec()` to bypass the
     import system entirely (see this conversation's bash history).

## 10. What This File is NOT

- Not a substitute for reading `doc/thesis_proposal.tex` and
  `doc/thesis_milestone.tex`. Those are authoritative; this file is
  navigational.
- Not a frozen plan — update sections §3 (pre-thesis checklist) and §4
  (what's built) after each working session. §1 (identity), §2
  (locked-in decisions), and §6 (working conventions) should change
  rarely; if you change them, note why in the commit message.
- Not a public README. It assumes the reader is either Zilong or an
  AI continuing his work, with full access to the proposal and
  milestone files.
