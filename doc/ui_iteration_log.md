# UI Iteration Log — Design Refinements from Instrumented Walkthroughs

**Purpose.** Primary-source record of usability findings and design changes to
the VA prototype, for the thesis chapters on *System Design* and *Iterative
Refinement*. Each entry: how the issue was observed → what changed → the
design rationale. Mirrors the thesis's own premise: defects in an analysis
tool are discovered the same way annotation-guideline defects are — by
running the loop and watching where it breaks.

**Method.** Two instrumented sessions (2026-06-10). Session 1: gap analysis
against Schmidt et al.'s poster/paper and the proposal, followed by an
append-only build-out. Session 2: a live walkthrough of the running app in
Chrome (screenshots, console + network inspection, scripted interactions),
fixing issues in place and re-testing immediately. All changes verified with
git diff; pytest suite kept green (39/39) throughout.

---

## Session 1 — Gap closure against the published concept (2026-06-10)

### 1.1 The closed loop did not physically exist
- **Observation (code audit).** Re-running extraction with a revised guideline
  *overwrote* the v1 facts (`data/facts/<model>/<doc>.json`) and Phase-2
  output (`data/conflicts/<doc>.json`). `distribution_shift` searched for
  `<doc>__v2_*.json`, which no code path ever wrote. The "Compare v1↔v2"
  button — the thesis's principal evaluation — could never return data.
- **Change.** Versioned artifact scheme: non-v1 facts → `data/facts__<version>/`,
  non-v1 conflicts → `data/conflicts/<doc>__<version>.json`
  (`run_phase2.run(..., out_suffix=)`); new `pipeline` job kind chains
  Phase-1 → Phase-2 in one background job (`POST /api/run_pipeline`).
- **Rationale.** An experiment whose baseline is destroyed by the treatment
  run is not an experiment. Version isolation is the precondition for the
  Phase-4 distribution-shift evaluation.

### 1.2 Poster views absent from the prototype
- **Observation (gap analysis).** The published concept (Dissecting Atomic
  Facts, Fig. 1–2) centres on: text-anchored highlighting (present), a
  semantic-similarity heatmap (absent), a fact-count/granularity histogram
  (absent), and small-multiple knowledge graphs (absent — only one merged KG).
- **Change.** Centre pane became tabbed: **Graph | Multiples | Heatmap |
  Stats**. Heatmap = full cosine matrix per annotator pair with Hungarian
  matches outlined by conflict colour (`/api/similarity_matrix`). Stats =
  per-annotator fact-count bars, per-section count table with spread
  highlighting, Jaccard IAA table (`/api/iaa`). Multiples = one mini-KG per
  annotator, ghosting non-owned entities.
- **Rationale.** These are the views in which the *disagreement signal* is
  legible: the heatmap exposes semantic disagreement, the histogram exposes
  granularity disagreement, the multiples expose structural interpretation
  differences (poster: "comparison through small multiples").

### 1.3 Small multiples share one layout
- **Design decision.** Positions are computed once on the union graph
  (headless concentric layout), then re-used as a `preset` layout in every
  panel; entities an annotator never touches stay visible but ghosted
  (opacity 0.13).
- **Rationale.** Small multiples only support comparison when the spatial
  frame is constant (Tufte; Munzner's "eyes beat memory"). Independent
  layouts would turn structural differences into layout noise. Ghost nodes
  preserve the reference frame and make *absence* visible — an annotator not
  extracting an entity IS the disagreement.

### 1.4 Conflicts were inspectable but not accountable
- **Observation.** The pair modal showed conflicts; nothing recorded *why*
  they happened. Authoring guideline v2 would have relied on the analyst's
  memory — exactly the "flat list is not actionable" failure the proposal
  criticises.
- **Change.** Review queue (drawer tab): per-pair side-by-side inspection,
  attribution to specific guideline rules (checkboxes parsed from the
  `### N.` headings inside the prompt's `<guideline>` block), free-text
  note, agree/relabel/dismiss, auto-advance. Persisted per doc
  (`data/reviews/`); `/api/review_summary` aggregates into the Experiment
  tab's evidence panel ("§7 implicated 12× — 9 granularity").
- **Rationale.** Closes the diagnostic loop: visualization → judgement →
  *recorded evidence* → revision. The evidence panel turns v2 authoring from
  recollection into tallied observation, and doubles as thesis material
  (which rules were under-specified, with examples).

---

## Session 2 — Live walkthrough findings (2026-06-10, agent-driven browser test)

### 2.1 Single-doc buttons silently operated on the whole corpus  ⚠ trust/safety
- **Observation (live).** "Build KG" enqueued Phase-2 but the job covered
  more than the current doc. Code audit: the buttons sent `doc_subset`, the
  backend reads `doc_ids` — the filter fell through silently, so "Extract
  here" would have launched Phase-1 on **all 51 parsed docs** (~4 h of
  unintended GPU work on the 6 GB card).
- **Change.** Parameter name aligned (`doc_ids`) in both buttons.
- **Rationale.** Scope of effect must match the scope the control implies.
  Silent fan-out is the most expensive class of UI defect when actions cost
  minutes of GPU time each — and it would have polluted the experiment's
  fact matrix mid-run.

### 2.2 Top-bar conflict counts were permanently zero
- **Observation (live).** After Phase-2 completed, the facts table and KG
  showed conflicts, but the top bar read `0 contra · 0 gran · 0 redu · 89 unl`.
  Audit: `/api/facts` only resolved per-fact edge labels when a conflict
  *filter* was active; unfiltered (the default), every fact reported
  `unlabeled`.
- **Change.** Edge labels now resolved unconditionally.
  Result: `3 contra · 40 gran · 38 redu · 8 unl`.
- **Rationale.** The overview indicator is the analyst's first read of a
  document's conflict mass ("overview first" — Shneiderman). An encoding
  that contradicts the detail views erodes trust in every other encoding.

### 2.3 Absolute-positioned view containers covered the pane header
- **Observation (live).** Opening Multiples hid the view-switcher tabs —
  the new containers used `position:absolute; inset:0`, covering the header
  inside the pane.
- **Change.** Containers are flex siblings of `#cy` (same layout contract),
  not overlays.
- **Rationale.** New views must inherit the pane's existing layout system
  rather than float above it; the mode switcher must never be occluded by
  the mode it switches.

### 2.4 Multiples: nodes too small to identify or hit
- **Observation (live).** 16 px unlabeled nodes were hard to click (the
  scripted tester missed twice) and impossible to identify without clicking.
- **Change.** Nodes 20 px; hovering a node shows its entity label and
  annotator count in the panel header; tapping syncs the selection across
  all panels *and* the main graph.
- **Rationale.** Fitts's law on target size; canvas rendering has no native
  tooltips, so the hover surface is relocated to the panel header. Selection
  sync preserves the cross-view identity contract the rest of the tool
  follows ("same object, same highlight, everywhere").

### 2.5 Heatmap: overview squeezed by fixed reserve
- **Observation (live).** The matrix area reserved a fixed 150 px for the
  detail card, leaving 4 of 30 rows visible.
- **Change.** Heatmap pane is a flex column: matrix gets all free space,
  detail card capped at 45% and scrolls.
- **Rationale.** The matrix *is* the overview; the detail card is on-demand
  ("details on demand"), so the overview holds the space budget.

### 2.6 Review/Experiment unusable at default drawer height
- **Observation (live).** The bottom drawer's default ~140 px showed the
  queue but no detail panel.
- **Change.** Activating Review (420 px) or Experiment (360 px) grows the
  drawer once; a height the user has dragged larger is never shrunk.
- **Rationale.** Space allocation should follow task demands; review is a
  reading-and-judging task, not a status glance. One-way growth respects
  explicit user override (no fighting the splitter).

### 2.7 Experiment defaulted to a stale machine-named guideline
- **Observation (live).** The guideline dropdown defaulted to
  `v2_dea3f65182c0` (an artifact of an old ad-hoc job) instead of `v2`.
- **Change.** Default prefers human-named versions (`v2`, `v3`) over
  machine-suffixed job artifacts (`v2_<hash>`).
- **Rationale.** Defaults are a statement of intent: the object of study is
  the curated revision, not a worker by-product.

### 2.8 Verified end-to-end during the walkthrough
- Build-KG → live job panel → KG render (real SBERT on the user's machine;
  stub Layer-2): 106 aligned pairs, antonym-trap contradictions surfaced
  with Layer-1 reasons (`numeric mismatch: A=['0,5'] B=['1,2']`).
- Heatmap cell → detail card → "review →" cross-link lands on the same pair
  in the Review queue, pre-selected.
- Two reviews saved (rule §7, §3) → evidence panel tallies them live
  (`2 reviews · §7 ×2 · §3 ×1`), auto-advance to next unreviewed pair.
- Closed-loop pipeline ran end-to-end from the Experiment tab
  (v2 × {qwen3.5:4b, gemma3:4b} × train-000000): qwen 25 facts (202 s,
  vs 32 under v1 — the v2 anti-split rules visibly reduce fact count),
  gemma 14 facts (87 s), Phase-2 0.3 s, and the v1↔v2 comparison modal
  opened automatically on completion. Two independent v2 runs produced
  29 vs 30 aligned pairs — extraction nondeterminism is ~±1 pair on this
  doc, worth reporting alongside Phase-4 numbers.

### 2.9 Pipeline robustness: transient Ollama 500s killed whole cells
- **Observation (live).** The first real v2 pipeline run failed on its first
  cell: `qwen3.5:4b` → HTTP 500 from Ollama `/api/generate`. The identical
  request succeeded seconds later (and the full cell succeeded on re-run) —
  a transient eviction/load-window failure, expected behaviour on a 6 GB
  card that swaps 4B models.
- **Change.** `extractor._post` retries transient failures (HTTP 5xx,
  connection refused) twice with exponential backoff before raising; 4xx
  still fails fast.
- **Rationale.** On consumer hardware, transient model-management hiccups
  are part of the operating envelope, not exceptional conditions. A
  multi-minute batch must not lose a cell to a 3-second window.

### 2.10 Partial extraction fabricated a perfect-convergence result  ⚠ validity
- **Observation (live).** When the qwen cell failed, the pipeline continued
  to Phase-2 with only gemma's facts. A single-annotator "comparison" has
  zero aligned pairs, so the resulting v2 conflicts file read as *all
  conflicts resolved* — a fabricated 100% convergence that the compare view
  would happily display.
- **Change.** `run_phase2.run()` now refuses (`SystemExit`) when fewer than
  2 annotators are present, naming the likely cause ("did an extraction
  cell fail?").
- **Rationale.** The most dangerous failure mode of an evaluation tool is
  not crashing — it is *silently producing a number that looks like
  success*. Guards must sit where the number is born, not in the UI.

### 2.11 Annotator-set mismatch makes deltas unreadable  ⚠ validity
- **Observation (live).** First real closed-loop result for
  `train-000000`: 106 → 30 pairs (−71.7%), granularity −86.7%,
  redundancy −95%, L1 filter rate 0.556 → 0.222. Superficially a huge
  guideline win — but the v1 baseline contains 3 annotators (one real
  qwen run + two *synthetic* sims) while v2 contains 2 real models. Three
  annotator pairs vs one mechanically inflates the v1 pair count; the
  delta is mostly composition, not guideline effect.
- **Change.** Both compare endpoints now return `v1_annotators` /
  `v2_annotators` / `annotators_match`; the compare modal shows a warning
  banner when the sets differ, telling the analyst to re-run the baseline
  with the same model set before reading deltas.
- **Rationale.** The tool's core claim is *measured* guideline improvement;
  the measurement view must therefore refuse to let an invalid comparison
  pass as a result. (Action item for the real Phase-4: rebuild the v1
  baseline for the experiment docs with the same real model set.)

### 2.12 Long extraction cells showed no intra-cell progress
- **Observation (live).** A qwen cell runs ~3.5 min (~10 sections); the
  jobs panel showed `0/3` the whole time — indistinguishable from a hang.
- **Change.** `extract()` accepts an `on_progress(section_i, n, path)`
  callback; the pipeline writes it into `job.progress.detail`
  (`"qwen3.5:4b × train-000000 · section 4/10 (preamble.recitals[3])"`),
  surfaced live in the Experiment status line.
- **Rationale.** Feedback latency should match the user's decision horizon:
  "is it working?" must be answerable within seconds, not at cell
  boundaries (Nielsen: visibility of system status).

### 2.13 Compare button died after page reload
- **Observation (live).** After a reload, "Compare vs v1" was disabled and
  inert even though results existed on disk — it was bound to the job id
  held in the previous page's memory.
- **Change.** The button now falls back to the currently selected guideline
  version; it is enabled whenever a non-v1 version is selected (the
  endpoint self-explains when no data exists yet).
- **Rationale.** UI state must be derivable from persistent state; any
  control that only works in the session that created its data will
  confuse the analyst who returns tomorrow.

### 2.14 Dev-server reloader is unreliable on the mounted volume
- **Observation (live).** `flask --debug`'s stat reloader picked up some
  backend edits but not others during the session (JS/CSS always fresh —
  they are read per request). Python fixes 2.9/2.10/2.12 therefore only
  activate after a manual server restart; one in-flight job risk window was
  also identified (a reloader restart drops the in-memory job queue).
- **Change.** Process note (no code): restart Flask manually after backend
  edits; never edit backend files while a pipeline job is running. Future
  work: persist the job queue to disk so restarts are harmless.
- **Rationale.** Recorded because it shaped the testing protocol; queue
  persistence is the structural fix and is deferred deliberately.

### 2.15 Method note: agent-driven UI testing
Scripted browser interaction (screenshot diffing, console/network capture,
JS-dispatched events) located 7 defects in one session at near-zero cost to
the developer. Two practical lessons: (a) element references go stale across
reloads — dispatching DOM events is more reliable than coordinate clicks for
regression checks; (b) native `confirm()` dialogs block automation and were
shimmed during tests — a reason to move destructive confirmations into DOM
modals in future work.

---

### 2.16 One filter state for all panes; text annotations now separable
- **Observation (user).** "文本界面要可以筛选显示哪个模型的标注，不然混在一起看不清"
  — three annotators' highlights interleave in the text pane; the per-fact
  rainbow colouring (old default) made overlap unreadable, and the top-bar
  annotator chips were too far from where the reading happens. The KG
  ignored the chips entirely.
- **Change.** (a) Default colouring switched to per-annotator. (b) The
  colour legend inside the text pane became the filter: click = toggle an
  annotator everywhere, double-click = solo. It proxies the top-bar chips
  via DOM clicks, so text, facts table AND the KG follow one filter state.
  (c) `refreshGraphHighlights` now applies the annotator selection to the
  graph. Verified: solo qwen → facts 32/89, KG hides 34/92 elements,
  top-bar counts re-tally, legend shows struck-through off-annotators.
- **Rationale.** One selection, every view (brushing consistency). Filters
  must live where the question arises — the analyst is *reading text* when
  they wonder "who said this?"; the affordance belongs in the text pane.

### 2.17 KG: filter means hide, labels truncate, focus on demand
- **Observation (user).** "kg视图也是看不明白" — 35+ nodes with full-length
  legal-entity labels in one merged view; the conflict filter only dimmed
  non-matching edges, so clutter stayed.
- **Change.** Conflict + annotator filters now *hide* non-matching edges
  and orphaned nodes (`display:none`, not opacity). Node labels truncate at
  24 chars with a hover readout strip under the canvas (full label, surface
  count, annotators; edges show label + conflict + annotator pair).
  Double-tap a node = 1-hop focus (everything else hidden, camera fits);
  double-tap background = restore. Verified: conflict=contradiction
  collapses the graph from 92 elements to 1 edge + 2 nodes — the antonym
  pair — with the 3 involved facts isolated in the table.
- **Rationale.** Filtering by removal beats dimming once element count
  exceeds what preattentive vision can segregate; "details on demand"
  (hover/focus) replaces permanently-visible labels that nobody can read
  anyway.

### 2.18 Hovering the KG yanked the camera to the global view
- **Observation (user).** "放大后鼠标移到一个节点就跳动和缩放到全局视角" —
  the new hover readout wrote text into the legend row; the row's height
  changed; a ResizeObserver on `#cy` (added for the pane splitters) ran
  `cy.fit()` on *any* container resize — so every hover re-fit the camera.
- **Change.** (a) The hover readout is now an absolute overlay on the canvas
  (zero layout impact, `pointer-events:none`). (b) The ResizeObserver only
  re-fits when the size delta exceeds 30 px (real splitter drags), and still
  calls `cy.resize()` for small reflows.
- **Rationale.** Never tie camera state to layout side-effects; an
  interaction that *reads* (hover) must not *move* the viewport. Defence in
  depth: remove the trigger AND de-sensitise the listener.

### 2.19 S/P/O bands missed paraphrased roles; now fuzzy with honesty marker
- **Observation (user).** "点击高亮要主谓宾三色显示" — the painting existed
  but exact substring search only: decontextualized facts paraphrase the
  source, so objects (worst case) rarely matched and the red band silently
  vanished.
- **Change.** Two-pass matching: exact left-to-right first, then a
  token-window fuzzy locator (≥55% token overlap, incl. number tokens like
  "2,0") for missed roles; overlapping bands clipped. Fuzzy bands render
  with a *dashed* underline + tooltip "approximate match" — when a model
  mis-copies a number, the band lands on the closest supporting text and
  the dashes warn it is not verbatim.
- **Rationale.** Visual joins between derived data (facts) and source text
  must degrade gracefully, but never silently pretend approximation is
  exactness — the dashed encoding keeps the provenance honest.

### 2.20 Finding: "complex SPO / sparse annotation" is mostly model, not v2
- **Question (user).** Sparse coverage and complex S/P/O even under v2 —
  guideline failure or model failure?
- **Measurement (train-000000, real runs).**
  | run | n_facts | obj tokens (avg) | obj verbatim in source | sections hit |
  |---|---|---|---|---|
  | v1 qwen3.5:4b | 32 | 7.3 | 26/32 (81%) | 8 |
  | v2 qwen3.5:4b | 25 | **6.0** | 22/25 (88%) | 9 |
  | v2 gemma3:4b  | **14** | **8.3** | **6/14 (43%)** | 9 |
- **Reading.** v2 *does* steer: qwen's objects got shorter (7.3→6.0 tokens),
  splits dropped (32→25), verbatim rate rose. The "messy" impression traces
  to gemma: half the recall (14 vs 25), heavy paraphrasing (43% verbatim),
  and a cross-sentence number-binding error ("0,4 %" — a real number from
  the cyclically-adjusted-deficit sentence — bound to the 2006 headline
  forecast whose true value is 1,6 %). No outright invented numbers in
  either model. Residual object length is partly *by design*: §3/§7 demand
  minimum-sufficient modifiers (the Molecular-Facts decontextualization
  trade-off the proposal treats as a guideline-controlled parameter).
- **Consequence.** Use the Review queue's dismiss/relabel + rule tagging to
  separate guideline-fixable cases from model-bound errors; bring phi4-mini
  into the v2 matrix to test whether gemma is the outlier. Candidate v3
  rule if object length still bothers: "object = single noun phrase; move
  qualifying clauses to natural_language" — but measure what it does to
  decontextualization before adopting.

### 2.21 Multiples: hover readout reflowed the mini-canvases
- **Observation (user).** Same failure class as 2.18, different surface: the
  per-panel hover label could wrap the panel header to two lines, shrinking
  the mini canvas underneath on every hover → visible jumping.
- **Change.** Panel headers have a fixed 26 px height; hover text is
  single-line with ellipsis. (2.18's lesson generalised: any hover-driven
  text surface must own a fixed box.)
- **Rationale.** Same as 2.18 — reads must not move the view. Recorded
  separately because it shows the *pattern* recurring in a second component;
  the thesis can cite it as a reusable design rule.

### 2.22 KG fragmentation: clause-objects bury the hub structure
- **Observation (user).** "支离破碎，主谓宾很复杂看不明白" — even good
  extractions produce object nodes that are whole clauses ("a further
  decline in ... 2,0 % of GDP in 2005"). Such nodes never merge in entity
  resolution, so the KG is dominated by single-fact star fragments;
  concentric layout arranged the fragments in rings, amplifying the chaos.
- **Change.** Three readability measures: (a) default layout → fcose, which
  force-packs disconnected components; (b) degree-aware labelling — on
  graphs >25 nodes only hubs (degree ≥2) and selected/SPO nodes keep labels,
  leaves are read via hover/selection; (c) a "Hide leaf islands" toggle
  (default ON) drops components with ≤2 visible nodes from the view, leaving
  the connected core where entities actually interact.
- **Rationale.** The analytical object is *inter-annotator disagreement
  around shared entities* — single-fact fragments carry no comparison
  signal and can be summoned on demand (toggle off / conflict filter).
  This is a view-level fix for a representation-level property: clause-y
  objects are partly guideline-by-design (§3/§7, see 2.20), so the KG must
  be readable *despite* them.

### 2.23 Pane maximize / collapse
- **Observation (user).** Three fixed panes cramp every task: reading text
  wants width, the KG wants area, reviewing facts wants the table. Splitter
  dragging exists but is fiddly for "give me just this pane for a minute".
- **Change.** Every pane header gets ⛶ (maximize/restore) and — (collapse to
  a 28 px strip; click the strip to restore). Cytoscape canvases re-measure
  on every layout change; the 2.18 resize-guard keeps the camera sane.
- **Rationale.** Focus+context at the workspace level: temporary exclusive
  attention to one view without losing the others' state.

### 2.24 Template cache served stale HTML while static files were fresh
- **Observation (live).** After 2.22's HTML edits: the graph rendered with
  the new fcose default (app.js fresh) but the new checkbox/buttons were
  missing — `GET /` returned the old markup. Jinja's compiled-template cache
  missed the on-disk change (mount mtime semantics; non-debug runs never
  check). Static files are read per request, hence the split-brain.
- **Change.** `TEMPLATES_AUTO_RELOAD=True`, `jinja_env.auto_reload=True`,
  `SEND_FILE_MAX_AGE_DEFAULT=0` in app.py — template freshness no longer
  depends on --debug. (Requires one final manual restart to take effect.)
- **Rationale.** Completes the dev-loop reliability work of 2.14: JS, CSS,
  templates and Python now all hot-reload on this machine's mounted volume.

### 2.25 Finding: the poster's KG assumes short facts; ours are decontextualized
- **Question (user).** "法律条文基本每句话都是新的主谓宾；poster 的节点是
  没修饰的名词 — 是不是 poster 的设定不适用于现在的场景?"
- **Analysis.** Largely yes, and it is quantifiable. The poster's Graph-View
  (Schmidt et al., service-bw administrative texts) draws nodes like
  "Pay fee" / "Get access" — minimal noun phrases, conditionals as
  structural If/Or nodes, and the graph is a *local* small-multiple around
  one ambiguity, not a whole-document KG. Our pipeline stacks three choices
  that each inflate entity uniqueness: EUR-Lex legal prose (dates, ratios,
  instrument numbers in nearly every sentence) × Molecular-Facts
  decontextualization (guideline §2/§3 *mandates* the modifiers) × a
  document-level conflict KG. Decontextualized mentions almost never share
  a surface form, SBERT clustering at 0.78 cannot bridge them, and the KG
  fragments: train-000000 baseline = 57 nodes in 14 components, largest
  component 14 nodes, 6 single-fact islands. **Decontextualization trades
  KG mergeability for fact self-containedness** — a structural tension
  worth a thesis subsection, not a tool bug. (The proposal anticipated the
  consequence implicitly: the unit of analysis is the *aligned pair*, not
  KG structure, precisely because entity-level graph comparison is
  brittle.)
- **Change.** Two-level entity identity: clustering now runs on a
  *core-noun-phrase* key (`normalize_entity`: strip determiners, date
  tails, cut clause objects at the first preposition past token 2) while
  facts and node `surface_forms` keep the full decontextualized strings —
  poster-style nodes on top, Molecular-Facts underneath. UI: "Core
  entities" toggle (default ON) in graph settings; `/api/graph?core=`
  recomputes and the cache is keyed on (threshold, core). Sandbox
  measurement (TF-IDF fallback, conservative): components 14→12, largest
  component 14→21 nodes (+50% connected core); SBERT should merge harder.
- **Rationale.** When a published visual concept meets a harder text genre,
  adapt the *identity function*, not the visualization: the poster's bare-
  noun nodes are recoverable as an equivalence class over decontextualized
  mentions. The toggle keeps both worlds comparable for the write-up.

### 2.26 Per-fact quality audit: qwen v1 vs v2 (see companion doc)
Full audit in **`annotation_quality_qwen_v1_v2.md`**: v2 fixed 3½ of the 4
targeted v1 patterns (null-object abrogation now fully decontextualized from
the doc title; modifier splits gone; adverbial objects 34%→12%; entity
confusion fixed) but over-corrected — 4 of 10 numeric facts lost their year
(§7: 92%→60%) and two facts dropped semantically required prepositions
("laid down [in]", "provided [by]"), inverting roles. Demonstratives not
eradicated (2→2). Drafted v3 candidate rules from the regressions. Key
methodological gain: quote/number fidelity is model-stable across versions
while boundary decisions move with the guideline — the loop separates
guideline-fixable from model-bound error classes.

### 2.27 Qualifiers move to the edge (Wikidata-style, display layer only)
- **Question (user).** "现在直接 SPO 对应节点-关系-节点是不是太简单了，
  导致定语无法分开、节点非常复杂?" — verdict: as the *measurement* unit the
  bare triple is sufficient (alignment runs on natural_language; IAA /
  distribution-shift never touch KG structure); as a *KG representation*
  it is genuinely under-expressive — binary relations cannot host the
  time/condition/agent qualifiers legal prose attaches to every clause
  (the 34% adverbial-object rate of v1 is the smoking gun). The mainstream
  homes for qualifiers: Wikidata statement qualifiers, W3C n-ary patterns,
  property-graph edge attributes.
- **Change (no annotation-schema change).** `src/entity_norm.split_entity`
  separates each S/O mention into core NP + qualifier list (date tails,
  clause tails); verified 100% parity with the previous normalize_entity on
  all regression cases. `kg_build` now attaches the stripped qualifiers to
  the EDGE (`data.qualifiers`, deduped, ≤6) — the statement carries its
  modifiers, the node stays a bare entity. Edge hover shows
  `[label] pred · annotators · ⟨in 2004; of 7 June 2005⟩`.
- **Escalation path documented.** A true fix at the annotation layer —
  optional `qualifiers:{time, condition, agent}` slots (Wikidata-style) —
  changes the atomic-fact unit definition and 4B schema-failure risk;
  parked as a supervisor-gated v3-schema experiment, measurable with the
  existing closed-loop machinery.
- **Ops note.** During verification the mounted-volume staleness bit in a
  new direction: a conflicts file written by the user-side Flask appeared
  truncated at a fixed byte offset from the sandbox while the server read
  it intact. Analysis of large user-written data files now goes through
  the HTTP API, not the mount (memory + working conventions updated).

### 2.28 Tuned force-directed profile + conditional-edge marking
- **Change (layout).** fcose upgraded from a generic single pass to a tuned
  profile: quality "proof", 5k iterations, randomize=true (fresh global
  arrangement), degree-scaled ideal edge length (hubs 1.5×, leaf pairs
  0.8×), degree-scaled repulsion, label-aware spacing, tiling for isolated
  nodes. Rationale: uniform force parameters treat a 6-degree hub and a
  leaf pair identically — exactly why hub neighbourhoods collapsed into
  label soup.
- **Change (logic visibility).** Edges whose riding facts contain
  conditional markers (if/when/unless/provided that/subject to/…) in quote
  or natural_language are flagged `conditional` by kg_build and rendered
  *dashed*, with a ⧟conditional badge in the hover readout. This is the
  poster's If/Or-node concept mapped onto our edge-centric KG without a
  schema change — v1 §6 deliberately keeps antecedents inside source_quote,
  so the signal is recoverable deterministically.

### 2.29 Genre-difficulty contrast: biography vs legal text (live demo)
- **Question (user).** "我是不是最好先拿传记类练手，现在的法律条文太复杂了?"
- **Experiment.** A 5-sentence Einstein biography was uploaded through the
  tool's own `+ Text` flow (doc `user-a60bde451b`) and run through the
  identical pipeline — same guideline v1, same models (qwen3.5:4b 9 facts,
  gemma3:4b 5 facts), same Phase-2 stub.
- **Result.** Biography: section coverage 100%/100%, char coverage 98%
  (qwen); IAA Jaccard 0.556, mean cosine 0.922; KG = ONE connected star of
  10 bare-noun nodes ("Einstein", "14 March 1879", "1921 Nobel Prize in
  Physics") — the poster aesthetic, zero fragmentation; conflicts are pure
  granularity/redundancy. Legal baseline on the same screen: char coverage
  50–57%, qwen↔gemma Jaccard ≈0.30 (v2), 42–57 nodes in 7–14 components
  with clause-shaped labels, plus contradictions.
- **Reading.** The pipeline, schema and guideline are NOT the bottleneck —
  the *genre* controls guideline ambiguity. FActScore-style biographies are
  easy precisely because their facts decontextualize into short SVO with
  recurring subjects; EUR-Lex is hard because qualifiers are load-bearing.
  Decision: corpus stays EUR-Lex (the difficulty IS the thesis signal, and
  the dry-run gate passed); biographies serve as (a) a free practice/
  calibration corpus via `+ Text`, (b) a 30-second easy-vs-hard demo for
  supervision meetings, (c) a thesis figure candidate (two KGs side by
  side: genre-dependent ambiguity).

## Open items (deliberately deferred)
- Neo4j persistence + cardinality-constraint editor (proposal Phase-3
  "patching") — out of current scope; graph JSON serving is sufficient for
  the single-analyst prototype.
- Heatmap row/col reordering (cluster-order vs document-order) — candidate
  for a future iteration if matrix reading proves hard on 50+ fact docs.
- Per-annotator colour identity is inconsistent between coverage chips and
  Stats bars — cosmetic; unify palette source later.
