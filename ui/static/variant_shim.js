/* ============================================================
 * variant_shim.js — MUST load before app.js.
 *
 * Makes the per-doc read endpoints "guideline-variant aware" without
 * touching app.js: when window.__variant is non-empty, GET requests to
 * the endpoints below get `variant=<v>` appended, so the existing
 * switchDoc() plumbing transparently loads e.g.
 * data/conflicts/<doc>__v2.json instead of the baseline file.
 *
 * The top-bar version dropdown (managed by analysis.js) sets
 * window.__variant and re-dispatches a change event on #doc-select.
 * ============================================================ */
(() => {
  window.__variant = "";
  const RX = /^\/api\/(facts|graph|pairs|coverage|similarity_matrix|iaa)\//;
  const orig = window.fetch.bind(window);
  window.fetch = (input, init) => {
    try {
      const url = typeof input === "string" ? input : input.url;
      if (window.__variant && RX.test(url)) {
        const patched = url + (url.includes("?") ? "&" : "?") +
          "variant=" + encodeURIComponent(window.__variant);
        input = typeof input === "string" ? patched : new Request(patched, input);
      }
    } catch (e) { /* never break fetch */ }
    return orig(input, init);
  };
})();
