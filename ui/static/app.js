/* ============================================================
 * Atomic Fact VA — front-end controller (extended for Phase-3).
 *
 * Hot keys:
 *    j / k             cycle through visible facts (down / up)
 *    Esc               close any open modal
 *
 * Modals
 *    Pair-detail modal   — opens when a Cytoscape EDGE is clicked
 *                          (shows every aligned-pair behind that edge,
 *                          both annotators side-by-side with their
 *                          natural_language + source_quote + cosine +
 *                          Layer-1/2 reason).
 *    Compare modal       — opens via the "Compare v1↔v2" top-bar button;
 *                          fetches /api/distribution_shift and renders
 *                          an inline-SVG bar chart of label counts.
 *
 * State machine (single global `state` object). All mutators go through
 * renderAll() so the panes stay coherent. The facts table is the
 * authoritative current-visible source; both text spans and the graph
 * are filtered by intersecting with `visibleFacts()`.
 * ============================================================ */

(() => {
const state = {
  doc: null,
  annotators: new Set(),
  allAnnotators: [],
  conflict: "",
  search: "",
  merge: 0.78,
  layout: "cose",
  selectedFactId: null,
  cy: null,
  textCache: { preamble: "", enacting: "" },
  factsById: new Map(),
  factSpans: new Map(),
  alignedPairs: [],          // for the pair-detail modal
  coverage: {},
  graph: null,
  jobs: new Map(),
};

const $ = (sel) => document.querySelector(sel);

// ============================================================
// Bootstrap
// ============================================================

async function boot() {
  const docs = (await fetch("/api/docs").then(r => r.json())).docs;
  const sel = $("#doc-select");
  for (const d of docs) {
    const opt = document.createElement("option");
    opt.value = d.doc_id;
    opt.textContent = `${d.doc_id} — ${truncate(d.title, 70)}`;
    sel.appendChild(opt);
  }
  sel.addEventListener("change", () => switchDoc(sel.value));

  $("#conflict-filter").addEventListener("change", (e) => {
    state.conflict = e.target.value;
    refreshText(); refreshFacts(); refreshGraphHighlights();
  });

  $("#search-box").addEventListener("input", (e) => {
    state.search = e.target.value.toLowerCase();
    refreshText(); refreshFacts();
  });

  $("#layout-select").addEventListener("change", (e) => {
    state.layout = e.target.value;
    if (state.cy) state.cy.layout({ name: state.layout, animate: false, padding: 18 }).run();
  });

  const slider = $("#merge-threshold");
  const sliderVal = $("#merge-threshold-value");
  let sliderTimer = null;
  slider.addEventListener("input", () => {
    state.merge = parseFloat(slider.value);
    sliderVal.textContent = state.merge.toFixed(2);
    clearTimeout(sliderTimer);
    sliderTimer = setTimeout(refreshGraph, 250);
  });

  $("#compare-btn").addEventListener("click", openCompareModal);
  $("#modal-close").addEventListener("click", closeAllModals);
  $("#compare-close").addEventListener("click", closeAllModals);
  $("#modal-overlay").addEventListener("click", (e) => { if (e.target.id === "modal-overlay") closeAllModals(); });
  $("#compare-overlay").addEventListener("click", (e) => { if (e.target.id === "compare-overlay") closeAllModals(); });

  $("#guideline-save").addEventListener("click", saveGuideline);
  $("#export-json").addEventListener("click", () => exportFacts("json"));
  $("#export-csv").addEventListener("click", () => exportFacts("csv"));

  document.addEventListener("keydown", onKeyDown);

  if (docs.length) switchDoc(docs[0].doc_id);
}

async function switchDoc(docId) {
  state.doc = docId;
  const [doc, factsResp, graph, coverage, pairs] = await Promise.all([
    fetch(`/api/doc/${docId}`).then(r => r.json()),
    fetch(`/api/facts/${docId}`).then(r => r.json()),
    fetch(`/api/graph/${docId}?merge_threshold=${state.merge}`).then(r => r.json()),
    fetch(`/api/coverage/${docId}`).then(r => r.json()).catch(() => ({})),
    fetch(`/api/pairs/${docId}`).then(r => r.json()).catch(() => []),
  ]);

  state.textCache.preamble = doc.preamble_text;
  state.textCache.enacting = doc.enacting_text;
  $("#doc-title").textContent = doc.title || docId;

  const annotators = new Set();
  for (const f of factsResp.facts) annotators.add(f._annotator);
  state.allAnnotators = [...annotators].sort();
  state.annotators = new Set(state.allAnnotators);
  renderChips();

  state.factsById = new Map(factsResp.facts.map(f => [f.fact_id, f]));
  state.alignedPairs = pairs;
  state.coverage = coverage;
  state.graph = graph;

  renderCoverageRow();
  renderText();
  renderFacts();
  renderGraph(graph);
}

// ============================================================
// Coverage row
// ============================================================

function renderCoverageRow() {
  const row = $("#coverage-row");
  row.innerHTML = "";
  for (const [ann, m] of Object.entries(state.coverage)) {
    const chip = document.createElement("span");
    chip.className = "cov-chip";
    const charPct = Math.round((m.char_coverage_frac || 0) * 100);
    const secPct  = Math.round((m.sections_hit_frac || 0) * 100);
    chip.innerHTML = `<span class="cov-ann">${escapeHtml(ann)}</span>
      sec ${secPct}% <span class="bar"><span style="width:${secPct}%"></span></span>
      ch ${charPct}% <span class="bar"><span style="width:${charPct}%"></span></span>
      ${m.n_facts}f`;
    row.appendChild(chip);
  }
}

// ============================================================
// Text pane
// ============================================================

function _container(sectionPath) {
  if (!sectionPath) return null;
  if (sectionPath.startsWith("preamble")) return "preamble";
  if (sectionPath.startsWith("enacting")) return "enacting";
  return null;
}

function renderText() {
  state.factSpans.clear();
  const visible = visibleFacts();
  const segments = { preamble: [], enacting: [] };
  for (const f of visible) {
    const loc = f.source_locator || {};
    const c = _container(loc.section_path);
    if (!c || loc.char_start == null || loc.char_end == null) continue;
    segments[c].push({
      start: loc.char_start, end: loc.char_end,
      fact_id: f.fact_id,
      conflict: f._edge_conflict_label || "unlabeled",
    });
  }

  const body = $("#doc-body");
  body.innerHTML = "";
  body.appendChild(renderSection("PREAMBLE", state.textCache.preamble, segments.preamble));
  body.appendChild(renderSection("ENACTING TEXT", state.textCache.enacting, segments.enacting));
}

function renderSection(name, text, segs) {
  const wrap = document.createElement("div");
  const h = document.createElement("div");
  h.className = "section-head";
  h.textContent = `— ${name} —`;
  wrap.appendChild(h);

  // Count overlapping spans at each cursor so we can stack underline layers.
  // The "overlap depth" of a fact within a section is the number of OTHER
  // facts whose span already overlaps. We render every fact as its own mark
  // (one click target per row) but bump CSS overlay layers for legibility.
  segs = segs.slice().sort((a, b) => a.start - b.start || a.end - b.end);
  // Pass 1: compute depth per seg (count of earlier segs whose end > seg.start)
  for (let i = 0; i < segs.length; i++) {
    let d = 0;
    for (let j = 0; j < i; j++) {
      if (segs[j].end > segs[i].start && segs[j].start < segs[i].end) d++;
    }
    segs[i].depth = d;
  }

  let cursor = 0;
  for (const seg of segs) {
    if (seg.start > cursor) wrap.appendChild(document.createTextNode(text.slice(cursor, seg.start)));
    const start = Math.max(seg.start, cursor);
    const end = Math.max(seg.end, start);
    if (end > start) {
      const m = document.createElement("mark");
      m.dataset.factId = seg.fact_id;
      m.className = seg.conflict + (seg.depth >= 2 ? " overlay-3" : seg.depth === 1 ? " overlay-2" : "");
      m.textContent = text.slice(start, end);
      m.addEventListener("click", () => selectFact(seg.fact_id));
      wrap.appendChild(m);
      if (!state.factSpans.has(seg.fact_id)) state.factSpans.set(seg.fact_id, m);
      cursor = end;
    }
  }
  if (cursor < text.length) wrap.appendChild(document.createTextNode(text.slice(cursor)));
  return wrap;
}

function refreshText() { renderText(); }

// ============================================================
// Annotator chips
// ============================================================

function renderChips() {
  const wrap = $("#annotator-chips");
  wrap.innerHTML = "";
  for (const a of state.allAnnotators) {
    const c = document.createElement("span");
    c.className = "chip on";
    c.textContent = a;
    c.addEventListener("click", () => {
      if (state.annotators.has(a)) state.annotators.delete(a); else state.annotators.add(a);
      c.classList.toggle("on");
      refreshText(); refreshFacts(); refreshGraphHighlights();
    });
    wrap.appendChild(c);
  }
}

// ============================================================
// Facts table
// ============================================================

function visibleFacts() {
  const out = [];
  const q = state.search;
  for (const f of state.factsById.values()) {
    if (!state.annotators.has(f._annotator)) continue;
    if (state.conflict && (f._edge_conflict_label || "unlabeled") !== state.conflict) continue;
    if (q) {
      const hay = `${f.subject || ""} ${f.predicate || ""} ${f.object || ""}`.toLowerCase();
      if (!hay.includes(q)) continue;
    }
    out.push(f);
  }
  return out;
}

function renderFacts() {
  const tbody = $("#facts-table tbody");
  tbody.innerHTML = "";
  const visible = visibleFacts();
  $("#facts-count").textContent = `${visible.length} visible / ${state.factsById.size} total`;

  for (const f of visible) {
    const tr = document.createElement("tr");
    tr.dataset.factId = f.fact_id;
    const conflict = f._edge_conflict_label || "unlabeled";
    tr.innerHTML = `
      <td class="annot">${escapeHtml(f._annotator)}</td>
      <td>${escapeHtml(f.subject || "")}</td>
      <td>${escapeHtml(f.predicate || "")}</td>
      <td>${escapeHtml(f.object || "")}</td>
      <td class="annot">${escapeHtml((f.source_locator || {}).section_path || "")}</td>
      <td class="conf ${conflict}">${conflict}</td>
      <td><button class="verify-btn ${f._verification?.status || ""}" data-fact-id="${f.fact_id}">
            ${f._verification?.status === "verified" ? "✓" : f._verification?.status === "rejected" ? "✗" : "?"}
          </button></td>`;
    tr.addEventListener("click", (e) => {
      if (e.target.classList.contains("verify-btn")) return;
      selectFact(f.fact_id);
    });
    tbody.appendChild(tr);
  }
  for (const btn of tbody.querySelectorAll(".verify-btn")) {
    btn.addEventListener("click", (e) => cycleVerify(e.target));
  }
  updateLabelCounts(visible);
}

function refreshFacts() { renderFacts(); }

function updateLabelCounts(visible) {
  const c = { contradiction: 0, granularity: 0, redundancy: 0, unlabeled: 0 };
  for (const f of visible) {
    const k = f._edge_conflict_label || "unlabeled";
    c[k] = (c[k] || 0) + 1;
  }
  $("#label-counts").textContent =
    `▮ ${c.contradiction} contra · ${c.granularity} gran · ${c.redundancy} redu · ${c.unlabeled} unl`;
}

async function cycleVerify(btn) {
  const cur = btn.classList.contains("verified") ? "verified"
            : btn.classList.contains("rejected") ? "rejected" : "";
  const next = { "": "verified", verified: "rejected", rejected: "" }[cur];
  const factId = btn.dataset.factId;
  const r = await fetch(`/api/verify/${state.doc}/${factId}`, {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ status: next === "" ? "unset" : next }),
  }).then(r => r.json());
  btn.classList.remove("verified", "rejected");
  if (r.status === "verified") { btn.classList.add("verified"); btn.textContent = "✓"; }
  else if (r.status === "rejected") { btn.classList.add("rejected"); btn.textContent = "✗"; }
  else { btn.textContent = "?"; }
}

// ============================================================
// Graph pane
// ============================================================

function renderGraph(graph) {
  if (state.cy) state.cy.destroy();
  state.cy = cytoscape({
    container: document.getElementById("cy"),
    elements: [...graph.nodes, ...graph.edges],
    layout: { name: state.layout, animate: false, padding: 18, idealEdgeLength: 110, nodeOverlap: 16 },
    style: [
      { selector: "node", style: {
          "background-color": "#cfd6e2", "label": "data(label)",
          "font-size": 10, "text-wrap": "wrap", "text-max-width": 90,
          "color": "#222", "text-valign": "center", "text-halign": "center",
          "width": "mapData(n_annotators, 1, 3, 18, 34)",
          "height": "mapData(n_annotators, 1, 3, 18, 34)",
          "border-width": 1, "border-color": "#94a0b6" }},
      { selector: "node[conflict_label = 'contradiction']", style: { "border-color": "#d32f2f", "border-width": 3 }},
      { selector: "node[conflict_label = 'granularity']",   style: { "border-color": "#f57c00", "border-width": 3 }},
      { selector: "node[conflict_label = 'redundancy']",    style: { "border-color": "#1976d2", "border-width": 2 }},
      { selector: "node.selected", style: { "background-color": "#fff3a0", "border-color": "#f5a623", "border-width": 4 }},
      { selector: "node.dimmed", style: { "opacity": 0.25 }},
      { selector: "edge", style: {
          "width": "mapData(n_annotators, 1, 3, 1.2, 3.2)",
          "line-color": "#9e9e9e", "target-arrow-color": "#9e9e9e",
          "target-arrow-shape": "triangle", "curve-style": "bezier",
          "label": "data(label)", "font-size": 8, "color": "#555",
          "text-rotation": "autorotate", "text-background-color": "#fff",
          "text-background-opacity": 0.7, "text-background-padding": 1 }},
      { selector: "edge[conflict_label = 'contradiction']", style: { "line-color": "#d32f2f", "target-arrow-color": "#d32f2f", "width": 3 }},
      { selector: "edge[conflict_label = 'granularity']",   style: { "line-color": "#f57c00", "target-arrow-color": "#f57c00", "width": 2.6 }},
      { selector: "edge[conflict_label = 'redundancy']",    style: { "line-color": "#1976d2", "target-arrow-color": "#1976d2", "width": 2.2 }},
      { selector: "edge.selected", style: { "line-color": "#f5a623", "target-arrow-color": "#f5a623", "width": 4 }},
      { selector: "edge.dimmed", style: { "opacity": 0.2 }},
    ],
  });

  state.cy.on("tap", "edge", (evt) => openPairModal(evt.target));
  state.cy.on("tap", "node", (evt) => filterByEntity(evt.target.id()));
  refreshGraphHighlights();
}

async function refreshGraph() {
  if (!state.doc) return;
  const url = `/api/graph/${state.doc}?merge_threshold=${state.merge}`;
  const graph = await fetch(url).then(r => r.json());
  state.graph = graph;
  renderGraph(graph);
}

function refreshGraphHighlights() {
  if (!state.cy) return;
  // Dim edges whose conflict label doesn't match the current filter.
  state.cy.elements().removeClass("dimmed");
  if (state.conflict) {
    state.cy.edges().forEach(e => {
      if (e.data("conflict_label") !== state.conflict) e.addClass("dimmed");
    });
  }
}

function filterByEntity(clusterId) {
  state.cy.elements().removeClass("selected");
  state.cy.getElementById(clusterId).addClass("selected");
  const node = state.cy.getElementById(clusterId);
  const surfaceForms = new Set(node.data("surface_forms") || []);

  const tbody = $("#facts-table tbody");
  for (const tr of tbody.querySelectorAll("tr")) {
    const f = state.factsById.get(tr.dataset.factId);
    const hit = f && (surfaceForms.has(f.subject) || surfaceForms.has(f.object));
    tr.style.display = hit ? "" : "none";
  }
}

// ============================================================
// Pair detail modal — opens on edge click
// ============================================================

function openPairModal(edge) {
  const factIds = new Set(edge.data("fact_ids") || []);
  // Find AlignedPair rows whose fact_a or fact_b is on this edge.
  const relevant = state.alignedPairs.filter(p =>
    factIds.has(p.fact_a_id) || factIds.has(p.fact_b_id));

  const body = $("#modal-body");
  $("#modal-title").textContent =
    `Edge: ${edge.data("source")} —[${edge.data("label")}]→ ${edge.data("target")}  ·  ${relevant.length} pairs`;
  body.innerHTML = "";

  if (relevant.length === 0) {
    // No aligned pair (single-annotator edge). Show every fact_id on the edge.
    for (const fid of factIds) {
      const f = state.factsById.get(fid);
      if (!f) continue;
      body.appendChild(renderSingleFactRow(f));
    }
  } else {
    for (const p of relevant) body.appendChild(renderPairRow(p));
  }
  $("#modal-overlay").classList.remove("hidden");
}

function renderPairRow(p) {
  const div = document.createElement("div");
  div.className = "pair-row";
  const lbl = p.conflict_label || "unlabeled";
  div.innerHTML = `
    <h4>
      <span>${escapeHtml(p.annotator_a)} ↔ ${escapeHtml(p.annotator_b)}</span>
      <span class="cos">cos ${(p.cosine ?? 0).toFixed(3)} · ${escapeHtml(p.status || "")}</span>
      <span class="lbl ${lbl}">${lbl}</span>
    </h4>`;
  const grid = document.createElement("div");
  grid.className = "side";
  for (const [side, fid, ann] of [["A", p.fact_a_id, p.annotator_a], ["B", p.fact_b_id, p.annotator_b]]) {
    const f = fid && state.factsById.get(fid);
    grid.innerHTML += `
      <div class="ann-tag">${escapeHtml(side)}: ${escapeHtml(ann)}</div>
      <div>
        <div class="nl">${escapeHtml(f?.natural_language || "(missing)")}</div>
        <div class="quote">"${escapeHtml((f?.source_locator || {}).quote || "")}"</div>
      </div>`;
  }
  div.appendChild(grid);
  if (p.layer1_reason || p.layer2_reason) {
    const r = document.createElement("div");
    r.className = "reasons";
    r.textContent = `L1: ${p.layer1_reason || "—"}   L2: ${p.layer2_reason || "—"}`;
    div.appendChild(r);
  }
  // Click anywhere in the row → also select the fact in the three panes.
  div.addEventListener("click", () => {
    if (p.fact_a_id) selectFact(p.fact_a_id);
    else if (p.fact_b_id) selectFact(p.fact_b_id);
  });
  return div;
}

function renderSingleFactRow(f) {
  const div = document.createElement("div");
  div.className = "pair-row";
  div.innerHTML = `
    <h4>
      <span>${escapeHtml(f._annotator)} (single-annotator edge)</span>
      <span class="lbl unlabeled">solo</span>
    </h4>
    <div class="side">
      <div class="ann-tag">${escapeHtml(f._annotator)}</div>
      <div>
        <div class="nl">${escapeHtml(f.natural_language || "")}</div>
        <div class="quote">"${escapeHtml((f.source_locator || {}).quote || "")}"</div>
      </div>
    </div>`;
  div.addEventListener("click", () => selectFact(f.fact_id));
  return div;
}

function closeAllModals() {
  $("#modal-overlay").classList.add("hidden");
  $("#compare-overlay").classList.add("hidden");
}

// ============================================================
// Compare modal — v1 vs v2 distribution shift
// ============================================================

async function openCompareModal() {
  const body = $("#compare-body");
  body.innerHTML = "<em>loading…</em>";
  $("#compare-overlay").classList.remove("hidden");
  try {
    const data = await fetch(`/api/distribution_shift/${state.doc}`).then(r => r.json());
    body.innerHTML = "";
    if (data.note) {
      body.innerHTML = `<p>${escapeHtml(data.note)}</p>`;
      return;
    }
    const max = Math.max(1, ...data.v1, ...data.v2);
    const grid = document.createElement("div");
    grid.className = "compare-bars";
    for (let i = 0; i < data.labels.length; i++) {
      const lbl = data.labels[i], v1 = data.v1[i], v2 = data.v2[i], dpct = data.delta_pct[i];
      grid.innerHTML += `
        <div class="lab"><span class="lbl ${lbl}" style="padding:1px 6px;border-radius:3px;">${lbl}</span></div>
        <div class="bar-track" title="v1=${v1}  v2=${v2}">
          <div class="bar-v1" style="width:${(v1/max*100).toFixed(1)}%"></div>
          <div class="bar-v2" style="width:${(v2/max*100).toFixed(1)}%"></div>
        </div>
        <div class="pct ${dpct == null ? '' : dpct < 0 ? 'dn' : 'up'}">${dpct == null ? '—' : (dpct>0?'+':'')+dpct+'%'}</div>`;
    }
    body.appendChild(grid);

    const summary = document.createElement("div");
    summary.className = "compare-summary";
    summary.innerHTML = `
      <div class="row"><b>Versions:</b> ${escapeHtml(data.v1_label)} → ${escapeHtml(data.v2_label || "—")}</div>
      <div class="row"><b>Total conflicts:</b> ${data.totals.v1} → ${data.totals.v2}
        (${data.totals.delta_pct == null ? '—' : (data.totals.delta_pct>0?'+':'')+data.totals.delta_pct+'%'})</div>
      <div class="row"><b>Layer-1 filter rate:</b>
        ${(data.layer1_rate.v1.layer1_filter_rate*100).toFixed(1)}%
        → ${(data.layer1_rate.v2.layer1_filter_rate*100).toFixed(1)}%</div>`;
    body.appendChild(summary);
  } catch (e) {
    body.innerHTML = `<em>Failed to load compare data: ${escapeHtml(e.message)}</em>`;
  }
}

// ============================================================
// Guideline editor + job poll
// ============================================================

async function saveGuideline() {
  const text = $("#guideline-text").value.trim();
  const models = $("#guideline-models").value.trim().split(",").map(s => s.trim()).filter(Boolean);
  const status = $("#guideline-status");
  status.classList.remove("err");
  if (!text) { status.textContent = "guideline text is empty"; status.classList.add("err"); return; }
  status.textContent = "enqueueing…";
  try {
    const resp = await fetch("/api/guideline", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ guideline_text: text, models }),
    });
    if (!resp.ok) {
      status.textContent = `error ${resp.status}`;
      status.classList.add("err");
      return;
    }
    const job = await resp.json();
    status.textContent = `enqueued: ${job.job_id} (${job.progress.total} cells)`;
    pollJob(job.job_id);
  } catch (e) {
    status.textContent = "request failed";
    status.classList.add("err");
  }
}

async function pollJob(jobId) {
  const node = $("#job-status");
  const line = document.createElement("div");
  line.className = "job-line";
  line.dataset.jobId = jobId;
  node.prepend(line);

  while (true) {
    let job;
    try {
      job = await fetch(`/api/jobs/${jobId}`).then(r => r.json());
    } catch (e) { break; }
    line.className = "job-line " + job.status;
    line.textContent = `[${jobId}] ${job.status} ${job.progress.done}/${job.progress.total}` +
                       (job.error ? ` — ${job.error}` : "");
    if (job.status === "done" || job.status === "failed") break;
    await new Promise(r => setTimeout(r, 2000));
  }
}

// ============================================================
// Cross-view selection
// ============================================================

function selectFact(factId) {
  state.selectedFactId = factId;
  for (const m of document.querySelectorAll("#doc-body mark.linked")) m.classList.remove("linked");
  const m = state.factSpans.get(factId);
  if (m) { m.classList.add("linked"); m.scrollIntoView({ behavior: "smooth", block: "center" }); }

  const tbody = $("#facts-table tbody");
  for (const tr of tbody.querySelectorAll("tr")) tr.classList.remove("selected");
  const row = tbody.querySelector(`tr[data-fact-id="${factId}"]`);
  if (row) { row.classList.add("selected"); row.scrollIntoView({ behavior: "smooth", block: "nearest" }); }

  if (state.cy) {
    state.cy.elements().removeClass("selected");
    const matchingEdges = state.cy.edges().filter(e => (e.data("fact_ids") || []).includes(factId));
    matchingEdges.addClass("selected");
    matchingEdges.connectedNodes().addClass("selected");
    if (matchingEdges.length) state.cy.animate({ fit: { eles: matchingEdges.connectedNodes(), padding: 50 } }, { duration: 300 });
  }
}

// ============================================================
// Keyboard navigation
// ============================================================

function onKeyDown(e) {
  if (e.key === "Escape") { closeAllModals(); return; }
  // Don't intercept when typing in an input or textarea
  const tag = (e.target.tagName || "").toLowerCase();
  if (tag === "input" || tag === "textarea") return;

  if (e.key === "j" || e.key === "k") {
    const facts = visibleFacts();
    if (!facts.length) return;
    let idx = facts.findIndex(f => f.fact_id === state.selectedFactId);
    if (idx < 0) idx = e.key === "j" ? -1 : facts.length;
    const next = e.key === "j" ? (idx + 1) % facts.length : (idx - 1 + facts.length) % facts.length;
    selectFact(facts[next].fact_id);
    e.preventDefault();
  }
}

// ============================================================
// Export
// ============================================================

function exportFacts(format) {
  const rows = visibleFacts();
  let blob, ext;
  if (format === "json") {
    blob = new Blob([JSON.stringify(rows, null, 2)], { type: "application/json" });
    ext = "json";
  } else {
    const headers = ["fact_id", "annotator", "subject", "predicate", "object", "natural_language",
                     "section_path", "char_start", "char_end", "conflict_label", "verification"];
    const lines = [headers.join(",")];
    for (const f of rows) {
      const loc = f.source_locator || {};
      const cells = [
        f.fact_id, f._annotator, f.subject, f.predicate, f.object,
        f.natural_language, loc.section_path,
        loc.char_start, loc.char_end,
        f._edge_conflict_label, f._verification?.status || "",
      ];
      lines.push(cells.map(csvCell).join(","));
    }
    blob = new Blob([lines.join("\n")], { type: "text/csv" });
    ext = "csv";
  }
  const a = document.createElement("a");
  a.href = URL.createObjectURL(blob);
  a.download = `facts_${state.doc}_visible.${ext}`;
  a.click();
}

function csvCell(v) {
  if (v == null) return "";
  const s = String(v);
  if (/[,"\n]/.test(s)) return '"' + s.replaceAll('"', '""') + '"';
  return s;
}

// ============================================================
// Helpers
// ============================================================

function truncate(s, n) { return s && s.length > n ? s.slice(0, n) + "…" : s; }
function escapeHtml(s) {
  return String(s ?? "")
    .replaceAll("&", "&amp;").replaceAll("<", "&lt;").replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;").replaceAll("'", "&#39;");
}

boot();
})();

/* ===========================================================
 * Phase-5 additions: append-only block. Reaches into the IIFE
 * above through the global `window` shim we install below so the
 * additions can stay self-contained without re-writing the file.
 * =========================================================== */

(() => {
const $ = (sel) => document.querySelector(sel);
const $$ = (sel) => document.querySelectorAll(sel);
const escapeHtml = (s) => String(s ?? "")
  .replaceAll("&","&amp;").replaceAll("<","&lt;").replaceAll(">","&gt;")
  .replaceAll('"',"&quot;").replaceAll("'","&#39;");

// State global to Phase-5 features. Independent of the IIFE state above.
const ext = {
  guidelines: [],         // [{version, summary, size}]
  models: ["qwen3.5:4b", "gemma3:4b", "phi4-mini"],
  matrixCols: [],         // [{model, guideline}]
  docs: [],
  matrixStatus: {},       // key="doc|model" -> {fact_count, guideline_version}
  spanSelection: null,    // {start, end, text}
};

document.addEventListener("DOMContentLoaded", initPhase5);
// Also fire now in case DOM is already ready (script is at end of body).
if (document.readyState !== "loading") initPhase5();

let _initialised = false;
function initPhase5() {
  if (_initialised) return;
  _initialised = true;

  // Drawer tab switching
  for (const tab of $$("#drawer-tabs .tab")) {
    tab.addEventListener("click", () => {
      const key = tab.dataset.tab;
      $$("#drawer-tabs .tab").forEach(t => t.classList.toggle("active", t === tab));
      $$(".tab-pane").forEach(p => p.classList.toggle("active", p.dataset.tab === key));
      if (key === "guideline-manager") loadGuidelinesList();
      if (key === "run-matrix") renderRunMatrix();
    });
  }

  // Upload modal wiring
  $("#upload-btn")?.addEventListener("click", openUploadModal);
  $("#upload-close")?.addEventListener("click", closeUploadModal);
  $("#upload-overlay")?.addEventListener("click", (e) => {
    if (e.target.id === "upload-overlay") closeUploadModal();
  });
  $("#upload-submit")?.addEventListener("click", submitUpload);

  // Span re-extract floater
  document.addEventListener("mouseup", maybeShowSpanMenu);
  $("#span-cancel")?.addEventListener("click", hideSpanMenu);
  $("#span-reextract")?.addEventListener("click", submitSpanReextract);

  // Guideline manager
  $("#gm-load")?.addEventListener("click", gmLoad);
  $("#gm-save")?.addEventListener("click", gmSave);

  // Run matrix
  $("#rm-add-combo")?.addEventListener("click", addMatrixCombo);
  $("#rm-submit")?.addEventListener("click", submitMatrix);

  // Pre-fill guidelines list (need it for span menu + upload modal too)
  refreshGuidelinesIntoSelects();
}

// ----------------------------------------------------------
// Upload modal
// ----------------------------------------------------------
function openUploadModal() {
  // refresh guideline + model selects
  refreshGuidelinesIntoSelects();
  $("#upload-overlay")?.classList.remove("hidden");
  $("#upload-title").value = "";
  $("#upload-textarea").value = "";
  $("#upload-status").textContent = "";
  $("#upload-status").className = "";
}
function closeUploadModal() { $("#upload-overlay")?.classList.add("hidden"); }

async function submitUpload() {
  const title = $("#upload-title").value.trim();
  const text = $("#upload-textarea").value;
  const split_mode = $("#upload-split").value;
  const extract = $("#upload-extract").checked;
  const model = $("#upload-model").value;
  const guideline_version = $("#upload-guideline").value;
  const status = $("#upload-status");
  if (!text.trim()) { status.textContent = "text is empty"; status.className = "err"; return; }
  status.textContent = "uploading…"; status.className = "";
  try {
    const r = await fetch("/api/upload_text", {
      method: "POST",
      headers: {"Content-Type": "application/json"},
      body: JSON.stringify({ title, text, split_mode, extract, model, guideline_version }),
    });
    if (!r.ok) throw new Error(`HTTP ${r.status}`);
    const data = await r.json();
    status.textContent = `uploaded: ${data.doc_id} (${data.n_sections} sections)` +
                         (data.job_id ? ` · extraction job ${data.job_id}` : "");
    status.className = "ok";
    // Refresh the doc dropdown — re-fetch the list and switch to the new doc
    const docs = (await fetch("/api/docs").then(r => r.json())).docs;
    const sel = $("#doc-select");
    sel.innerHTML = "";
    for (const d of docs) {
      const opt = document.createElement("option");
      opt.value = d.doc_id;
      opt.textContent = `${d.is_user_doc ? "📝 " : ""}${d.doc_id} — ${d.title?.slice(0,70) || ""}`;
      sel.appendChild(opt);
    }
    sel.value = data.doc_id;
    sel.dispatchEvent(new Event("change"));
    setTimeout(closeUploadModal, 600);
  } catch (e) {
    status.textContent = `failed: ${e.message}`; status.className = "err";
  }
}

// ----------------------------------------------------------
// Span re-extract
// ----------------------------------------------------------
function _getDocBodySelection() {
  const sel = window.getSelection();
  if (!sel || sel.isCollapsed) return null;
  const body = $("#doc-body");
  if (!body) return null;
  const range = sel.getRangeAt(0);
  if (!body.contains(range.commonAncestorContainer)) return null;
  // Compute char offsets relative to the whole doc-body innerText.
  const pre = document.createRange();
  pre.selectNodeContents(body);
  pre.setEnd(range.startContainer, range.startOffset);
  const start = pre.toString().length;
  const end = start + range.toString().length;
  const text = range.toString();
  if (text.trim().length < 5) return null;
  return { start, end, text, rect: range.getBoundingClientRect() };
}

function maybeShowSpanMenu(e) {
  // Don't trigger when clicking inside the menu itself.
  if (e.target.closest("#span-menu")) return;
  const sel = _getDocBodySelection();
  if (!sel) { hideSpanMenu(); return; }
  ext.spanSelection = sel;
  // Populate model + guideline selects
  refreshGuidelinesIntoSelects();
  const menu = $("#span-menu");
  menu.classList.remove("hidden");
  const paneRect = $("#pane-text").getBoundingClientRect();
  // Position just below the selection
  menu.style.left = `${Math.max(8, sel.rect.left - paneRect.left)}px`;
  menu.style.top = `${sel.rect.bottom - paneRect.top + 4}px`;
}
function hideSpanMenu() {
  ext.spanSelection = null;
  $("#span-menu")?.classList.add("hidden");
}

async function submitSpanReextract() {
  if (!ext.spanSelection) return;
  const docSelect = $("#doc-select");
  const doc_id = docSelect?.value;
  if (!doc_id) return;
  const model = $("#span-model").value;
  const guideline_version = $("#span-guideline").value;
  // Note: char_start/end here are offsets into the rendered text — for user
  // docs (which have only preamble_text), this is correct. For EUR-Lex docs
  // the section_path form is safer; the backend supports both.
  const body = {
    doc_id, model, guideline_version,
    char_start: ext.spanSelection.start,
    char_end: ext.spanSelection.end,
  };
  try {
    const r = await fetch("/api/reextract_span", {
      method: "POST", headers: {"Content-Type":"application/json"},
      body: JSON.stringify(body),
    });
    if (!r.ok) {
      const t = await r.text();
      alert(`span re-extract failed: ${r.status} ${t.slice(0,200)}`);
      return;
    }
    const data = await r.json();
    hideSpanMenu();
    // Reload the doc to surface the new facts
    docSelect.dispatchEvent(new Event("change"));
    setTimeout(() => alert(`Span re-extracted: ${data.n_new_facts} new facts written for ${data.model}.`), 200);
  } catch (e) {
    alert(`span re-extract error: ${e.message}`);
  }
}

// ----------------------------------------------------------
// Guideline manager
// ----------------------------------------------------------
async function loadGuidelinesList() {
  try {
    const data = await fetch("/api/guidelines").then(r => r.json());
    ext.guidelines = data.guidelines || [];
    const sel = $("#gm-select");
    sel.innerHTML = "";
    for (const g of ext.guidelines) {
      const opt = document.createElement("option");
      opt.value = g.version;
      opt.textContent = `${g.version} (${g.size}b)`;
      sel.appendChild(opt);
    }
    refreshGuidelinesIntoSelects();
  } catch (e) {
    $("#gm-status").textContent = `list failed: ${e.message}`;
    $("#gm-status").className = "err";
  }
}

async function gmLoad() {
  const version = $("#gm-select").value;
  if (!version) return;
  try {
    const data = await fetch(`/api/guidelines/${encodeURIComponent(version)}`).then(r => r.json());
    $("#gm-text").value = data.text || "";
    $("#gm-new-version").value = version;
    $("#gm-status").textContent = `loaded ${version} (${data.size}b)`;
    $("#gm-status").className = "ok";
  } catch (e) {
    $("#gm-status").textContent = `load failed: ${e.message}`;
    $("#gm-status").className = "err";
  }
}

async function gmSave() {
  const version = ($("#gm-new-version").value || "").trim();
  const text = $("#gm-text").value;
  const status = $("#gm-status");
  if (!version) { status.textContent = "new version is required"; status.className = "err"; return; }
  if (!/^[A-Za-z0-9_.\-]+$/.test(version)) {
    status.textContent = "version must be alphanumeric/dot/dash/underscore"; status.className = "err"; return;
  }
  try {
    const r = await fetch(`/api/guidelines/${encodeURIComponent(version)}`, {
      method: "PUT", headers: {"Content-Type":"application/json"},
      body: JSON.stringify({ text }),
    });
    if (!r.ok) {
      const t = await r.text();
      status.textContent = `save failed: ${r.status} ${t.slice(0,160)}`;
      status.className = "err";
      return;
    }
    const data = await r.json();
    status.textContent = `saved ${data.version} (${data.size}b)`;
    status.className = "ok";
    await loadGuidelinesList();
  } catch (e) {
    status.textContent = `save failed: ${e.message}`;
    status.className = "err";
  }
}

// ----------------------------------------------------------
// Run matrix
// ----------------------------------------------------------
async function renderRunMatrix() {
  // Fetch docs, guidelines, matrix status
  const [docs, gl, status] = await Promise.all([
    fetch("/api/docs").then(r => r.json()),
    fetch("/api/guidelines").then(r => r.json()),
    fetch("/api/run_matrix/status").then(r => r.json()),
  ]);
  ext.docs = docs.docs || [];
  ext.guidelines = gl.guidelines || [];
  ext.matrixStatus = status || {};
  if (!ext.matrixCols.length) {
    // Default columns: every model × every guideline. The user can prune.
    const models = ($("#rm-models").value || "").split(",").map(s => s.trim()).filter(Boolean);
    ext.matrixCols = [];
    for (const m of models) for (const g of ext.guidelines) {
      ext.matrixCols.push({ model: m, guideline: g.version });
    }
  }
  drawMatrix();
}

function drawMatrix() {
  const wrap = $("#rm-matrix-wrap");
  wrap.innerHTML = "";
  if (!ext.matrixCols.length || !ext.docs.length) {
    wrap.innerHTML = "<p class='hint'>No docs or columns yet. Add models above and click 'Add'.</p>";
    return;
  }
  const tbl = document.createElement("table");
  tbl.className = "rm-matrix";
  const thead = document.createElement("thead");
  let h = "<tr><th>doc</th>";
  for (const c of ext.matrixCols) h += `<th title="${escapeHtml(c.model)} · ${escapeHtml(c.guideline)}">${escapeHtml(c.model.slice(0,12))}<br><small>${escapeHtml(c.guideline)}</small></th>`;
  h += "</tr>";
  thead.innerHTML = h;
  tbl.appendChild(thead);
  const tbody = document.createElement("tbody");
  for (const d of ext.docs) {
    const tr = document.createElement("tr");
    tr.innerHTML = `<td class="doc-id">${d.is_user_doc ? "📝 " : ""}${escapeHtml(d.doc_id)}</td>`;
    for (const c of ext.matrixCols) {
      const key = `${d.doc_id}|${c.model}`;
      const cur = ext.matrixStatus[key];
      const done = cur && cur.guideline_version === c.guideline;
      const title = cur ? `existing: ${cur.fact_count}f from ${cur.guideline_version}` : "no prior run";
      tr.innerHTML += `<td class="cell${done ? " done" : ""}" title="${escapeHtml(title)}">
        <input type="checkbox" data-doc="${escapeHtml(d.doc_id)}"
               data-model="${escapeHtml(c.model)}" data-guideline="${escapeHtml(c.guideline)}"></td>`;
    }
    tbody.appendChild(tr);
  }
  tbl.appendChild(tbody);
  wrap.appendChild(tbl);
}

function addMatrixCombo() {
  const models = ($("#rm-models").value || "").split(",").map(s => s.trim()).filter(Boolean);
  ext.matrixCols = [];
  for (const m of models) for (const g of ext.guidelines) {
    ext.matrixCols.push({ model: m, guideline: g.version });
  }
  drawMatrix();
}

async function submitMatrix() {
  const checks = $$('#rm-matrix-wrap input[type="checkbox"]:checked');
  const cells = [...checks].map(el => ({
    doc_id: el.dataset.doc,
    model: el.dataset.model,
    guideline_version: el.dataset.guideline,
  }));
  const status = $("#rm-status");
  if (!cells.length) { status.textContent = "select at least one cell"; status.className = "err"; return; }
  status.textContent = `queueing ${cells.length} cells…`; status.className = "";
  try {
    const r = await fetch("/api/run_matrix", {
      method: "POST", headers: {"Content-Type":"application/json"},
      body: JSON.stringify({ cells }),
    });
    if (!r.ok) { status.textContent = `failed: HTTP ${r.status}`; status.className = "err"; return; }
    const data = await r.json();
    const ok = data.jobs.filter(j => j.job_id).length;
    const err = data.jobs.length - ok;
    status.textContent = `queued ${ok} jobs (${err} skipped/errored)`;
    status.className = ok ? "ok" : "err";
  } catch (e) {
    status.textContent = `failed: ${e.message}`; status.className = "err";
  }
}

// ----------------------------------------------------------
// Shared: populate model + guideline selects everywhere
// ----------------------------------------------------------
async function refreshGuidelinesIntoSelects() {
  let glList = ext.guidelines;
  if (!glList.length) {
    try {
      const data = await fetch("/api/guidelines").then(r => r.json());
      glList = data.guidelines || [];
      ext.guidelines = glList;
    } catch { glList = []; }
  }
  const guidelineSelects = [$("#span-guideline"), $("#upload-guideline")];
  for (const s of guidelineSelects) {
    if (!s) continue;
    const prev = s.value;
    s.innerHTML = "";
    for (const g of glList) {
      const opt = document.createElement("option");
      opt.value = g.version; opt.textContent = g.version;
      s.appendChild(opt);
    }
    if (prev && [...s.options].some(o => o.value === prev)) s.value = prev;
  }
  const modelSelects = [$("#span-model"), $("#upload-model")];
  for (const s of modelSelects) {
    if (!s) continue;
    const prev = s.value;
    s.innerHTML = "";
    for (const m of ext.models) {
      const opt = document.createElement("option");
      opt.value = m; opt.textContent = m;
      s.appendChild(opt);
    }
    if (prev && [...s.options].some(o => o.value === prev)) s.value = prev;
  }
}
})();

/* ============================================================
 * Phase-5b additions: Live jobs polling + Phase-1/2 batch buttons.
 * Self-contained IIFE; reuses #live-jobs container in topbar and the
 * Background-runs drawer pane.
 * ============================================================ */
(() => {
const $ = (sel) => document.querySelector(sel);
const $$ = (sel) => document.querySelectorAll(sel);
const escapeHtml = (s) => String(s ?? "")
  .replaceAll("&","&amp;").replaceAll("<","&lt;").replaceAll(">","&gt;")
  .replaceAll('"',"&quot;").replaceAll("'","&#39;");

let _pollTimer = null;
let _panelOpen = false;
let _knownJobs = new Set();   // track new jobs so we auto-open the panel

document.addEventListener("DOMContentLoaded", initLiveJobs);
if (document.readyState !== "loading") initLiveJobs();
let _initialised = false;
function initLiveJobs() {
  if (_initialised) return;
  _initialised = true;
  $("#jobs-toggle")?.addEventListener("click", toggleJobsPanel);

  // Background-runs buttons
  $("#run-phase1-btn")?.addEventListener("click", runPhase1);
  $("#run-phase2-btn")?.addEventListener("click", runPhase2);

  // Populate guideline dropdown for Phase-1 form
  populateP1Guidelines();

  // Start polling on a 2-second cadence regardless of panel visibility
  // (so a new job auto-opens the panel). Stop only when no in-flight jobs.
  startPolling();
}

async function populateP1Guidelines() {
  try {
    const data = await fetch("/api/guidelines").then(r => r.json());
    const sel = $("#p1-guideline");
    if (!sel) return;
    sel.innerHTML = "";
    for (const g of data.guidelines || []) {
      const opt = document.createElement("option");
      opt.value = g.version; opt.textContent = g.version;
      sel.appendChild(opt);
    }
    sel.value = "v1";
  } catch {}
}

function toggleJobsPanel() {
  const panel = $("#live-jobs");
  _panelOpen = panel.classList.toggle("hidden") ? false : true;
  $("#jobs-toggle").textContent = _panelOpen ? "Jobs ▴" : "Jobs ▾";
  if (_panelOpen) refreshJobsOnce();
}

function startPolling() {
  if (_pollTimer) return;
  const tick = async () => {
    try {
      const haveLive = await refreshJobsOnce();
      // Keep polling forever — the cost is one tiny request every 2s. The
      // benefit is the panel becomes "live" the moment a job appears even
      // if the user kicked it off via a separate flow (run_matrix etc.).
      // We do throttle if nothing is in-flight: slow to 10s.
      const interval = haveLive ? 2000 : 10000;
      _pollTimer = setTimeout(tick, interval);
    } catch {
      _pollTimer = setTimeout(tick, 5000);
    }
  };
  tick();
}

async function refreshJobsOnce() {
  let jobs;
  try {
    jobs = (await fetch("/api/jobs").then(r => r.json())).jobs || [];
  } catch { return false; }

  // Auto-open panel if a new job appeared
  let haveLive = false;
  let newSeen = false;
  const ids = new Set();
  for (const j of jobs) {
    ids.add(j.job_id);
    if (j.status === "queued" || j.status === "running") haveLive = true;
    if (!_knownJobs.has(j.job_id)) newSeen = true;
  }
  _knownJobs = ids;
  if (newSeen && !_panelOpen) {
    _panelOpen = true;
    $("#live-jobs")?.classList.remove("hidden");
    if ($("#jobs-toggle")) $("#jobs-toggle").textContent = "Jobs ▴";
  }

  const panel = $("#live-jobs");
  if (!panel) return haveLive;
  panel.innerHTML = "";
  for (const j of jobs.slice(0, 20)) {
    const pct = j.progress.total ? Math.round(100 * j.progress.done / j.progress.total) : 0;
    const row = document.createElement("div");
    row.className = "job-row";
    row.dataset.jobId = j.job_id;
    row.innerHTML = `
      <span class="kind ${j.kind}">${j.kind}</span>
      <span class="lbl" title="${escapeHtml(j.label || j.job_id)}">${escapeHtml(j.label || j.job_id)}</span>
      <div class="progress" title="${j.progress.done}/${j.progress.total}">
        <div class="bar" style="width:${pct}%"></div>
        <span class="label">${j.progress.done}/${j.progress.total} (${pct}%)</span>
      </div>
      <div style="display:flex;gap:4px;justify-content:flex-end;">
        <span class="status ${j.status}" title="${escapeHtml(j.error || '')}">${j.status}</span>
        ${j.status === "queued" ? `<button class="cancel-btn" data-job="${j.job_id}">×</button>` : ""}
      </div>`;
    panel.appendChild(row);
  }
  for (const b of panel.querySelectorAll(".cancel-btn")) {
    b.addEventListener("click", () => cancelJob(b.dataset.job));
  }
  return haveLive;
}

async function cancelJob(jobId) {
  try {
    const r = await fetch(`/api/jobs/${jobId}/cancel`, { method: "POST" });
    if (!r.ok) alert(`cancel failed: HTTP ${r.status}`);
  } catch (e) { alert(`cancel failed: ${e.message}`); }
  refreshJobsOnce();
}

// ------------------------------------------------------------
// Phase-1 / Phase-2 batch buttons
// ------------------------------------------------------------
async function runPhase1() {
  const models = ($("#p1-models").value || "").split(",").map(s => s.trim()).filter(Boolean);
  const guideline_version = $("#p1-guideline").value || "v1";
  const subset = ($("#p1-doc-subset").value || "").split(",").map(s => s.trim()).filter(Boolean);
  const body = { models, guideline_version };
  if (subset.length) body.doc_ids = subset;

  const expectedDocs = subset.length || 50;  // best-effort estimate for the confirm
  const ok = confirm(
    `Phase-1 batch:\n` +
    `  models: ${models.join(", ")}\n` +
    `  guideline: ${guideline_version}\n` +
    `  ~${models.length} × ${expectedDocs} = ${models.length * expectedDocs} cells\n\n` +
    `On 6 GB GPU each cell is ~3-5 minutes — full matrix can take many hours.\n` +
    `Proceed?`
  );
  if (!ok) return;
  try {
    const r = await fetch("/api/run_phase1", {
      method: "POST", headers: {"Content-Type":"application/json"},
      body: JSON.stringify(body),
    });
    if (!r.ok) {
      const t = await r.text();
      alert(`failed: ${r.status} ${t.slice(0,160)}`);
      return;
    }
    const job = await r.json();
    alert(`Enqueued: job ${job.job_id} (${job.progress.total} cells). Monitor in the Live jobs panel.`);
    refreshJobsOnce();
  } catch (e) { alert(`request failed: ${e.message}`); }
}

async function runPhase2() {
  const skip_layer2 = $("#p2-skip-layer2").checked;
  const layer2_model = $("#p2-layer2-model").value || "qwen3.5:4b";
  const align_threshold = parseFloat($("#p2-align-threshold").value) || 0.78;
  const body = { skip_layer2, layer2_model, align_threshold };
  const ok = confirm(`Phase-2 batch:\n  skip_layer2: ${skip_layer2}\n` +
                     `  layer2_model: ${layer2_model}\n  align_threshold: ${align_threshold}\n\nProceed?`);
  if (!ok) return;
  try {
    const r = await fetch("/api/run_phase2", {
      method: "POST", headers: {"Content-Type":"application/json"},
      body: JSON.stringify(body),
    });
    if (!r.ok) {
      const t = await r.text();
      alert(`failed: ${r.status} ${t.slice(0,160)}`);
      return;
    }
    const job = await r.json();
    alert(`Enqueued: job ${job.job_id} (${job.progress.total} docs).`);
    refreshJobsOnce();
  } catch (e) { alert(`request failed: ${e.message}`); }
}
})();
