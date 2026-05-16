/**
 * LocalHire Agent — Page-context interceptor (MAIN world)
 *
 * Runs in the page's MAIN world (not isolated content-script world), so it can
 * monkey-patch window.fetch, XMLHttpRequest, and WebSocket. Captured payloads
 * are postMessage'd to the content script, which decides what to do.
 *
 * Why this matters: Workday/Greenhouse/Lever/Ashby/SmartRecruiters all emit
 * structured JSON for job postings BEFORE the DOM is rendered. Intercepting
 * those responses gives us canonical, low-latency, deterministic data — far
 * better than parsing rendered HTML.
 */
(() => {
  if (window.__lh_interceptor_installed) return;
  window.__lh_interceptor_installed = true;

  const TAG = "__lh_capture__";

  // URL patterns we care about — ATS API endpoints across known portals
  const JOB_URL_PATTERNS = [
    /\/api\/.*\/jobs?\b/i,
    /\/jobs?\/[0-9a-f]{8,}/i,
    /\/postings\b/i,
    /\/graphql\b/i,
    /\/job[_-]?posting/i,
    /\/career[s]?\/.*\b/i,
    /\/boards_api\b/i,           // Greenhouse
    /myworkdayjobs\.com.*\/job\b/i,
    /jobs\.lever\.co/i,
    /ashbyhq\.com\/api/i,
    /icims\.com.*\/jobs/i,
    /smartrecruiters\.com.*\/postings/i,
  ];

  function shouldCapture(url) {
    if (!url || typeof url !== "string") return false;
    if (url.length > 2000) return false; // pathological URLs
    return JOB_URL_PATTERNS.some(p => p.test(url));
  }

  // Truncate huge payloads — we only need enough to identify it as a job posting.
  const MAX_PAYLOAD_BYTES = 256 * 1024; // 256KB cap

  function dispatch(record) {
    try {
      window.postMessage({ source: TAG, record }, "*");
    } catch (e) { /* serialization failed, drop */ }
  }

  function tryParseJSON(text) {
    if (!text || typeof text !== "string") return null;
    const t = text.trim();
    if (!t || (t[0] !== "{" && t[0] !== "[")) return null;
    try { return JSON.parse(t); } catch { return null; }
  }

  // ─── fetch ──────────────────────────────────────────────────────────────────
  const origFetch = window.fetch;
  if (origFetch) {
    window.fetch = async function (resource, init) {
      let url;
      try { url = (typeof resource === "string") ? resource : (resource?.url || ""); } catch { url = ""; }

      const startedAt = performance.now();
      const response = await origFetch.apply(this, arguments);

      if (shouldCapture(url)) {
        // Clone before reading body — original consumer must still be able to .json()
        response.clone().text().then(text => {
          if (!text || text.length > MAX_PAYLOAD_BYTES) return;
          const json = tryParseJSON(text);
          dispatch({
            kind: "fetch",
            url,
            method: (init && init.method) || "GET",
            status: response.status,
            latencyMs: Math.round(performance.now() - startedAt),
            ts: Date.now(),
            payload: json || text.slice(0, 4096),
            payloadType: json ? "json" : "text",
          });
        }).catch(() => {});
      }
      return response;
    };
  }

  // ─── XMLHttpRequest ─────────────────────────────────────────────────────────
  const XHR = window.XMLHttpRequest;
  if (XHR) {
    const origOpen = XHR.prototype.open;
    const origSend = XHR.prototype.send;

    XHR.prototype.open = function (method, url) {
      this.__lh_url = url;
      this.__lh_method = method;
      this.__lh_started = performance.now();
      return origOpen.apply(this, arguments);
    };

    XHR.prototype.send = function () {
      if (shouldCapture(this.__lh_url)) {
        this.addEventListener("load", () => {
          try {
            const text = this.responseText;
            if (!text || text.length > MAX_PAYLOAD_BYTES) return;
            const json = tryParseJSON(text);
            dispatch({
              kind: "xhr",
              url: this.__lh_url,
              method: this.__lh_method || "GET",
              status: this.status,
              latencyMs: Math.round(performance.now() - this.__lh_started),
              ts: Date.now(),
              payload: json || text.slice(0, 4096),
              payloadType: json ? "json" : "text",
            });
          } catch (e) {}
        });
      }
      return origSend.apply(this, arguments);
    };
  }

  // ─── Hydration payload probe (one-shot, after DOM ready) ────────────────────
  function probeHydration() {
    const probes = {
      nextData:    () => {
        const el = document.getElementById("__NEXT_DATA__");
        return el ? tryParseJSON(el.textContent) : null;
      },
      nuxt:        () => window.__NUXT__ || null,
      apollo:      () => window.__APOLLO_STATE__ || null,
      appData:     () => window.__appData || null,
      initialState:() => window.__INITIAL_STATE__ || window.__INITIAL_DATA__ || null,
      // Ashby specifically
      ashby:       () => window.__appData?.posting || window.__appData?.job || null,
      // Generic JSON-LD JobPosting
      jsonLd: () => {
        const scripts = document.querySelectorAll('script[type="application/ld+json"]');
        for (const s of scripts) {
          const parsed = tryParseJSON(s.textContent);
          if (!parsed) continue;
          const items = Array.isArray(parsed) ? parsed : [parsed];
          for (const it of items) {
            if (it && /JobPosting|Job/i.test(it["@type"] || "")) return it;
          }
        }
        return null;
      },
    };
    const found = {};
    for (const [name, fn] of Object.entries(probes)) {
      try {
        const v = fn();
        if (v) found[name] = v;
      } catch (e) {}
    }
    if (Object.keys(found).length) {
      dispatch({ kind: "hydration", ts: Date.now(), payload: found, payloadType: "json" });
    }
  }

  // Run probe at multiple points — pages hydrate at different times
  if (document.readyState === "complete" || document.readyState === "interactive") {
    setTimeout(probeHydration, 100);
  } else {
    document.addEventListener("DOMContentLoaded", () => setTimeout(probeHydration, 100));
  }
  setTimeout(probeHydration, 1500);
  setTimeout(probeHydration, 4000);

  // Signal readiness
  dispatch({ kind: "ready", ts: Date.now() });
})();
