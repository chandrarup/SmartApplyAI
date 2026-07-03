// LocalHire Agent — Popup Script v2.1

// ─────────────────────────────────────────────
// EXTENSION LOGGER
// Toggle: Settings checkbox "Enable Extension Logs"
// Storage key: lh_log_enabled  (boolean, default true)
// Logs stored in: chrome.storage.local  lh_logs[]  (circular 200)
// Backend sink: POST /lh/ext-logs  (batched every 5 s)
// ─────────────────────────────────────────────
const LHLog = (() => {
  const LEVELS  = { DEBUG: 0, INFO: 1, WARN: 2, ERROR: 3 };
  const MAX_BUF = 200;
  let _enabled  = true;
  let _minLevel = LEVELS.INFO;
  let _buf      = [];
  let _timer    = null;

  // Load persisted settings
  try {
    chrome.storage.local.get(["lh_log_enabled", "lh_log_level", "lh_logs"], res => {
      if (res.lh_log_enabled !== undefined) _enabled = !!res.lh_log_enabled;
      if (res.lh_log_level)  _minLevel = LEVELS[res.lh_log_level] ?? LEVELS.INFO;
      if (Array.isArray(res.lh_logs)) _buf = res.lh_logs.slice(-MAX_BUF);
    });
  } catch (_) {}

  function _write(level, module, msg, data) {
    if (!_enabled || LEVELS[level] < _minLevel) return;
    const ts    = new Date().toISOString();
    const entry = { ts, level, module, msg, data: data ?? null };

    // Console
    const fn = level === "ERROR" ? console.error
             : level === "WARN"  ? console.warn
             : level === "DEBUG" ? console.debug
             : console.log;
    fn(`[LH][${level}][${module}] ${msg}`, data ?? "");

    // Circular buffer
    _buf.push(entry);
    if (_buf.length > MAX_BUF) _buf = _buf.slice(-MAX_BUF);

    // Persist to local storage (debounced)
    clearTimeout(_timer);
    _timer = setTimeout(() => {
      try { chrome.storage.local.set({ lh_logs: _buf }); } catch (_) {}
      _flush();
    }, 3000);
  }

  async function _flush() {
    if (!_buf.length) return;
    try {
      const apiUrl = document.getElementById("apiUrlInput")?.value?.trim() || "http://127.0.0.1:5001";
      const toSend = _buf.slice();
      await fetch(`${apiUrl}/lh/ext-logs`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ logs: toSend }),
        keepalive: true,
      });
    } catch (_) { /* backend offline — keep in buffer */ }
  }

  return {
    debug:  (mod, msg, d) => _write("DEBUG", mod, msg, d),
    info:   (mod, msg, d) => _write("INFO",  mod, msg, d),
    warn:   (mod, msg, d) => _write("WARN",  mod, msg, d),
    error:  (mod, msg, d) => _write("ERROR", mod, msg, d),

    setEnabled(val) {
      _enabled = val;
      try { chrome.storage.local.set({ lh_log_enabled: val }); } catch (_) {}
    },
    isEnabled: () => _enabled,
    getLogs:   () => _buf.slice(),
    flush:     _flush,
  };
})();

// ─────────────────────────────────────────────
// STATE
// ─────────────────────────────────────────────
let pageContext = null;
let analysisData = null;
let chatHistory = [];
let fillLogEntries = [];
let currentLlm = "ollama"; // "ollama" | "claude"
let lastDetectedCompany = "";
let lastDetectedPlatform = "";
let lastDetectedRole = "";
let currentTabId = null;
let approvedMatches = [];

// ─────────────────────────────────────────────
// SESSION PERSISTENCE (survives popup close/reopen)
// ─────────────────────────────────────────────
async function saveSession(updates) {
  if (!currentTabId) return;
  const key = `tab_${currentTabId}`;
  return new Promise(resolve => {
    chrome.storage.session.get([key], result => {
      const current = result[key] || {};
      chrome.storage.session.set({ [key]: { ...current, ...updates, ts: Date.now() } }, resolve);
    });
  });
}

async function loadSession() {
  if (!currentTabId) return null;
  return new Promise(resolve => {
    chrome.storage.session.get([`tab_${currentTabId}`], result => {
      const data = result[`tab_${currentTabId}`];
      if (data && (Date.now() - (data.ts || 0)) < 7200000) resolve(data);
      else resolve(null);
    });
  });
}

// ─────────────────────────────────────────────
// INIT
// ─────────────────────────────────────────────
document.addEventListener("DOMContentLoaded", async () => {
  await loadSettings();

  // Get current tab
  const tabs = await new Promise(r => chrome.tabs.query({ active: true, currentWindow: true }, r));
  currentTabId = tabs[0]?.id;
  LHLog.info("popup", "DOMContentLoaded", { tabId: currentTabId, url: tabs[0]?.url });

  detectPlatform();

  // Restore cached session state first (instant — no network).
  // On Greenhouse form pages, skip restoring cached context — it may be stale form text.
  const isOnGreenhouseForm = (() => {
    try {
      const u = new URL(tabs[0]?.url || "");
      return /greenhouse\.io/.test(u.hostname) && /embed\/job_app/i.test(u.pathname);
    } catch { return false; }
  })();
  const session = await loadSession();
  if (session?.pageContext && !isOnGreenhouseForm) {
    pageContext = session.pageContext;
  }
  if (session?.analysisData) {
    analysisData = session.analysisData;
    renderAnalysis(analysisData);
  }
  if (session?.chatHistory?.length) {
    chatHistory = session.chatHistory;
    const histDiv = document.getElementById("chat-history");
    histDiv.innerHTML = "";
    chatHistory.forEach(m => addBubble(m.content, m.role === "user" ? "user" : "bot"));
  }

  // Load page context: try background cache first (works cross-page), then live scrape.
  // Exception: on Greenhouse application form pages the cache holds form boilerplate,
  // not the JD — always go through scrapePageContext so the API adapter runs.
  if (!pageContext) {
    if (isOnGreenhouseForm) {
      scrapePageContext(() => loadFAQs());
    } else {
      chrome.runtime.sendMessage({ action: "get_page_context", tabId: currentTabId }, bgRes => {
        if (bgRes?.text) {
          pageContext = bgRes.text;
          saveSession({ pageContext });
          loadFAQs();
        } else {
          scrapePageContext(() => loadFAQs());
        }
      });
    }
  } else {
    loadFAQs();
  }
  loadApprovedMatches();

  // Listen for autofill progress from content script
  chrome.runtime.onMessage.addListener((msg) => {
    if (msg.action === "autofill_progress") handleFillProgress(msg.data);
  });
});

// ─────────────────────────────────────────────
// TAB SWITCHING
// ─────────────────────────────────────────────
document.querySelectorAll(".tab").forEach(tab => {
  tab.addEventListener("click", () => {
    document.querySelectorAll(".tab").forEach(t => t.classList.remove("active"));
    document.querySelectorAll(".panel").forEach(p => p.classList.remove("active"));
    tab.classList.add("active");
    document.getElementById(tab.dataset.panel).classList.add("active");
  });
});

// ─────────────────────────────────────────────
// HELPERS
// ─────────────────────────────────────────────
function showStatus(elId, type, message, spin = false) {
  const el = document.getElementById(elId);
  el.className = `status-bar ${type}`;
  el.innerHTML = spin ? `<div class="spinner"></div><span>${message}</span>` : message;
  el.classList.remove("hidden");
}

function hideEl(id) { document.getElementById(id)?.classList.add("hidden"); }
function showEl(id) { document.getElementById(id)?.classList.remove("hidden"); }

function getApiUrl() {
  return document.getElementById("apiUrlInput").value.trim() || "http://127.0.0.1:5001";
}

// Extension endpoint map (via main backend API URL):
// - GET /profile                (knowledge-backed profile source)
// - POST /autofill              (rule + LLM answer engine)
// - POST /autofill/learn        (host-specific corrections)
// - GET /matches/approved       (approved queue items for override fill)

function getLlm() {
  return document.getElementById("llmSelect").value || currentLlm;
}

async function loadSettings() {
  return new Promise(resolve => {
    chrome.runtime.sendMessage({ action: "get_settings" }, res => {
      if (res?.url) document.getElementById("apiUrlInput").value = res.url;
      if (res?.llm) {
        document.getElementById("llmSelect").value = res.llm;
        currentLlm = res.llm;
        updateModelPill(res.llm);
        toggleClaudeKeyRow(res.llm);
      }
      if (res?.claudeKey) document.getElementById("claudeKeyInput").value = res.claudeKey;
      // Sync log toggle UI with persisted state
      chrome.storage.local.get(["lh_log_enabled"], r => {
        const el = document.getElementById("logToggle");
        if (el) el.checked = r.lh_log_enabled !== false; // default true
      });
      resolve();
    });
  });
}

function updateModelPill(llm) {
  const pill = document.getElementById("modelPill");
  if (!pill) return;
  if (llm === "claude") {
    pill.textContent = "Claude";
    pill.className = "model-pill claude";
  } else {
    pill.textContent = "Ollama";
    pill.className = "model-pill";
  }
}

function toggleClaudeKeyRow(llm) {
  if (llm === "claude") {
    showEl("claudeKeyRow");
  } else {
    hideEl("claudeKeyRow");
  }
}

// ─────────────────────────────────────────────
// LLM SELECTOR LISTENER
// ─────────────────────────────────────────────
document.getElementById("llmSelect").addEventListener("change", (e) => {
  currentLlm = e.target.value;
  toggleClaudeKeyRow(currentLlm);
  updateModelPill(currentLlm);
});

// ─────────────────────────────────────────────
// SAVE SETTINGS
// ─────────────────────────────────────────────
document.getElementById("logToggle")?.addEventListener("change", e => {
  LHLog.setEnabled(e.target.checked);
  LHLog.info("settings", `Extension logging ${e.target.checked ? "enabled" : "disabled"}`);
});

document.getElementById("saveSettingsBtn").addEventListener("click", () => {
  const url = document.getElementById("apiUrlInput").value.trim();
  const llm = document.getElementById("llmSelect").value;
  const claudeKey = document.getElementById("claudeKeyInput").value.trim();
  LHLog.info("settings", "Settings saved", { url, llm });

  chrome.runtime.sendMessage({
    action: "save_settings",
    url: url || "http://127.0.0.1:5001",
    llm,
    claudeKey
  }, () => {
    currentLlm = llm;
    updateModelPill(llm);

    // Also persist Claude key to backend if set
    if (llm === "claude" && claudeKey) {
      fetch(`${getApiUrl()}/set-claude-key`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ key: claudeKey })
      }).catch(() => {});
    }

    const btn = document.getElementById("saveSettingsBtn");
    const orig = btn.textContent;
    btn.textContent = "Saved ✓";
    btn.style.background = "#dcfce7";
    btn.style.color = "#166534";
    setTimeout(() => {
      btn.textContent = orig;
      btn.style = "";
    }, 2000);
  });
});

// Greenhouse public API adapter — fetches real JD when on application form page
async function fetchGreenhouseJD(company, jobId) {
  LHLog.info("greenhouse", "Fetching JD from Greenhouse API", { company, jobId });
  const url = `https://boards-api.greenhouse.io/v1/boards/${company}/jobs/${jobId}`;
  const res = await fetch(url);
  if (!res.ok) {
    LHLog.error("greenhouse", `Greenhouse API error ${res.status}`, { company, jobId, url });
    throw new Error(`Greenhouse API ${res.status}`);
  }
  const data = await res.json();
  const title = data.title || "";
  const div = document.createElement("div");
  div.innerHTML = data.content || "";
  const bodyText = (div.textContent || "").replace(/\s+/g, " ").trim();
  const text = (title ? title + "\n\n" : "") + bodyText;
  LHLog.info("greenhouse", "JD fetched OK", { title, chars: text.length });
  // Surface role name into UI
  if (title) {
    lastDetectedRole = lastDetectedRole || title;
    const roleEl = document.getElementById("roleInput");
    if (roleEl && !roleEl.value) roleEl.value = title;
  }
  return text;
}

function withTimeout(promise, ms, fallback = null) {
  return Promise.race([
    promise,
    new Promise(resolve => setTimeout(() => resolve(fallback), ms)),
  ]);
}

/** Fast JD for Customize — avoids slow getBestJobContext() which can hang 60s+. */
async function fetchCustomizeContext() {
  if (pageContext && pageContext.length >= 50) {
    return {
      jd: pageContext,
      role: analysisData?.role || lastDetectedRole || "",
      company: document.getElementById("companyInput")?.value?.trim() || lastDetectedCompany || "",
      jdQuality: null,
      source: "popup-cache",
    };
  }

  const bg = await withTimeout(
    new Promise(resolve => {
      chrome.runtime.sendMessage({ action: "get_page_context", tabId: currentTabId }, resolve);
    }),
    3000,
    null,
  );
  if (bg?.jobContext?.jdText?.length >= 50) {
    return {
      jd: bg.jobContext.jdText,
      role: bg.jobContext.title || "",
      company: bg.jobContext.company || document.getElementById("companyInput")?.value?.trim() || "",
      jdQuality: bg.jobContext.jdQuality || null,
      source: bg.jobContext.sourceAdapter || "bg-jobContext",
    };
  }
  if (bg?.text?.length >= 50) {
    return {
      jd: bg.text,
      role: analysisData?.role || "",
      company: document.getElementById("companyInput")?.value?.trim() || lastDetectedCompany || "",
      jdQuality: null,
      source: "bg-text",
    };
  }

  if (!currentTabId) {
    return { jd: "", role: "", company: "", jdQuality: null, source: "none" };
  }

  const text = await withTimeout(
    new Promise(resolve => {
      chrome.tabs.sendMessage(currentTabId, { action: "get_text" }, r => {
        if (chrome.runtime.lastError) resolve("");
        else resolve(r?.text || "");
      });
    }),
    10000,
    "",
  );
  if (text.length >= 50) {
    pageContext = text;
    saveSession({ pageContext: text });
  }
  return {
    jd: text,
    role: analysisData?.role || lastDetectedRole || "",
    company: document.getElementById("companyInput")?.value?.trim() || lastDetectedCompany || "",
    jdQuality: null,
    source: text.length >= 50 ? "get_text" : "empty",
  };
}

function scrapePageContext(callback) {
  const finish = (text) => { if (callback) callback(text || ""); };
  if (pageContext) {
    LHLog.debug("scrape", "pageContext already cached", { chars: pageContext.length });
    finish(pageContext);
    return;
  }
  chrome.tabs.query({ active: true, currentWindow: true }, tabs => {
    const tab = tabs[0];
    if (!tab) { finish(""); return; }

    // Greenhouse application form — scraping returns a blank form, not the JD.
    // Detect the pattern and fetch the real JD from the Greenhouse public API.
    if (tab.url) {
      try {
        const u = new URL(tab.url);
        const isGreenhouseForm = /greenhouse\.io/.test(u.hostname) && /embed\/job_app/i.test(u.pathname);
        if (isGreenhouseForm) {
          const company = u.searchParams.get("for") || u.searchParams.get("board_token") || "";
          const token   = u.searchParams.get("token") || "";
          LHLog.info("scrape", "Greenhouse form detected — using API adapter", { company, token });
          if (company && token) {
            fetchGreenhouseJD(company, token)
              .then(text => {
                if (text && text.length > 50) {
                  pageContext = text;
                  saveSession({ pageContext });
                  LHLog.info("scrape", "pageContext set from Greenhouse API", { chars: text.length });
                  finish(pageContext);
                } else {
                  throw new Error("empty");
                }
              })
              .catch(err => {
                LHLog.error("scrape", "Greenhouse API failed — falling back to content script", { err: err?.message });
                chrome.tabs.sendMessage(tab.id, { action: "get_text" }, response => {
                  if (chrome.runtime.lastError || !response) {
                    LHLog.warn("scrape", "Fallback get_text failed", { err: chrome.runtime.lastError?.message });
                    finish("");
                    return;
                  }
                  pageContext = response.text;
                  saveSession({ pageContext });
                  LHLog.warn("scrape", "Fallback: form page text used", { chars: pageContext?.length });
                  finish(pageContext);
                });
              });
            return; // exit early — async path takes over
          }
        }
      } catch (_) { /* URL parse error — fall through to content script */ }
    }

    LHLog.debug("scrape", "Scraping page text via content script", { tabId: tab.id, url: tab.url });
    chrome.tabs.sendMessage(tab.id, { action: "get_text" }, response => {
      if (chrome.runtime.lastError || !response) {
        LHLog.error("scrape", "content script get_text failed", { err: chrome.runtime.lastError?.message });
        finish("");
        return;
      }
      pageContext = response.text;
      saveSession({ pageContext });
      LHLog.info("scrape", "pageContext set from page scrape", { chars: pageContext?.length });
      finish(pageContext);
    });
  });
}

function detectPlatform() {
  chrome.tabs.query({ active: true, currentWindow: true }, tabs => {
    if (!tabs[0]) return;
    chrome.tabs.sendMessage(tabs[0].id, { action: "get_platform" }, response => {
      if (chrome.runtime.lastError || !response) return;
      lastDetectedPlatform = response.platform || "";
      document.getElementById("platformBadge").textContent = lastDetectedPlatform;
      const title = tabs[0].title || "";
      const companyMatch = title.match(/(?:at|@)\s+(.+?)(?:\s*[-–|·]|$)/i);
      if (companyMatch) {
        lastDetectedCompany = companyMatch[1].trim();
        const el = document.getElementById("companyInput");
        if (el && !el.value) el.value = lastDetectedCompany;
      }
      const roleMatch = title.match(/^(.+?)\s+(?:at|@|-|–|\|)/);
      if (roleMatch) lastDetectedRole = roleMatch[1].trim();
    });
  });
}

// ─────────────────────────────────────────────
// MATCH TAB
// ─────────────────────────────────────────────
function renderAnalysis(data) {
  document.getElementById("matchScore").textContent = data.score || "—";
  document.getElementById("matchRole").textContent = data.role || "—";
  document.getElementById("matchSummary").textContent = data.tailored_summary || "";
  document.getElementById("missingSkill").textContent = data.missing_skill || "";
  const skillList = document.getElementById("matchedSkills");
  skillList.innerHTML = "";
  (data.skills_matched || []).forEach(s => {
    const tag = document.createElement("span");
    tag.className = "tag green"; tag.textContent = s;
    skillList.appendChild(tag);
  });
  if (data.missing_skill) {
    const tag = document.createElement("span");
    tag.className = "tag red"; tag.textContent = "✗ " + data.missing_skill;
    skillList.appendChild(tag);
  }
  if (data.role) document.getElementById("roleInput").value = data.role;
  hideEl("matchStatus");
  showEl("matchResult");
}

document.getElementById("analyzeBtn").addEventListener("click", () => {
  const btn = document.getElementById("analyzeBtn");
  btn.disabled = true;
  btn.textContent = "Analyzing...";
  hideEl("matchResult");
  showStatus("matchStatus", "loading", "Reading page & thinking...", true);
  LHLog.info("analyze", "Analyze button clicked", { llm: getLlm() });

  scrapePageContext(async text => {
    LHLog.info("analyze", "JD text ready for /analyze", { chars: text?.length, llm: getLlm() });
    try {
      const res = await fetch(`${getApiUrl()}/analyze`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ jd_text: text, llm: getLlm() })
      });
      const data = await res.json();
      if (!res.ok) throw new Error(data.detail || "Analyze failed");
      analysisData = data;
      saveSession({ analysisData });
      renderAnalysis(data);
      LHLog.info("analyze", "Analysis complete", { score: data.score, role: data.role });
    } catch (e) {
      LHLog.error("analyze", "Analyze request failed", { err: e?.message });
      showStatus("matchStatus", "error", "Error: " + e.message);
    } finally {
      btn.disabled = false;
      btn.textContent = "Analyze Job Description";
    }
  });
});

document.getElementById("pdfBtn").addEventListener("click", async () => {
  const btn = document.getElementById("pdfBtn");
  btn.disabled = true; btn.textContent = "Generating PDF...";
  try {
    const res = await fetch(`${getApiUrl()}/generate-pdf`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(analysisData)
    });
    if (res.ok) {
      const blob = await res.blob();
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url; a.download = "Resume_Tailored.pdf";
      document.body.appendChild(a); a.click(); document.body.removeChild(a);
      btn.textContent = "Downloaded ✓";
      setTimeout(() => { btn.disabled = false; btn.textContent = "Generate Tailored PDF Resume"; }, 3000);
    } else {
      btn.textContent = "PDF Failed (check local TeX tools)"; btn.disabled = false;
    }
  } catch (e) {
    btn.textContent = "Error: " + e.message; btn.disabled = false;
  }
});

// Customize Resume — POST JD to backend /pending-jd, then open dashboard
document.getElementById("customizeResumeBtn").addEventListener("click", async () => {
  const btn = document.getElementById("customizeResumeBtn");
  btn.disabled = true;
  btn.textContent = "Opening…";
  showStatus("matchStatus", "loading", "Preparing job description…", true);

  try {
    const ctx = await fetchCustomizeContext();
    const jd = (ctx.jd || "").slice(0, 8000);
    const role = ctx.role || (analysisData && analysisData.role) || lastDetectedRole || "";
    const company = document.getElementById("companyInput")?.value?.trim()
                    || ctx.company || lastDetectedCompany || "";
    const apiUrl = getApiUrl();
    const jdQuality = ctx.jdQuality || null;
    LHLog.info("customize", "customizeResumeBtn clicked", {
      jdLen: jd.length, role, company, apiUrl, source: ctx.source, jdQuality,
    });

    if (!jd || jd.length < 50) {
      hideEl("matchStatus");
      if (!confirm("No job description found on this page.\n\nOpen dashboard to paste the JD manually?")) {
        return;
      }
    } else if (jdQuality && jdQuality.ok === false) {
      const reason = (jdQuality.reasons && jdQuality.reasons[0]) || "Low-quality extraction.";
      if (!confirm(`JD quality is low (${jdQuality.score}/100): ${reason}\n\nContinue anyway?`)) {
        hideEl("matchStatus");
        return;
      }
    }

    showStatus("matchStatus", "loading", "Opening dashboard…", true);
    const pendingRes = await fetch(`${apiUrl}/pending-jd`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ jd, role, company, jd_quality: jdQuality }),
    });
    if (!pendingRes.ok) throw new Error(`Server returned ${pendingRes.status}`);
    const pending = await pendingRes.json().catch(() => ({}));
    LHLog.info("customize", "POST /pending-jd OK — opening dashboard");
    const token = pending?.token ? `&token=${encodeURIComponent(pending.token)}` : "";
    chrome.tabs.create({ url: `${apiUrl}/dashboard?from=extension${token}` });
    hideEl("matchStatus");
    showStatus("matchStatus", "success", "Dashboard opened — click Analyze Match when ready.");
  } catch (e) {
    LHLog.error("customize", "Customize failed", { err: e?.message });
    showStatus("matchStatus", "error", "Customize failed: " + e.message);
    try {
      const apiUrl = getApiUrl();
      chrome.tabs.create({ url: `${apiUrl}/dashboard?from=extension` });
    } catch (_) {}
  } finally {
    btn.disabled = false;
    btn.textContent = "✨ Customize Resume on Web";
  }
});

// ─────────────────────────────────────────────
// AUTOFILL TAB
// ─────────────────────────────────────────────
function startAutofillRun(extraPayload = {}) {
  const btn = document.getElementById("autofillBtn");
  btn.disabled = true; btn.textContent = "Filling...";
  fillLogEntries = [];
  hideEl("fillLog");
  hideEl("autofillStatus");
  showEl("progressWrap");
  document.getElementById("progressBar").style.width = "0%";
  document.getElementById("progressBar").style.background = "#007bff";
  document.getElementById("progressLabel").textContent = "Starting...";

  chrome.tabs.query({ active: true, currentWindow: true }, tabs => {
    chrome.tabs.sendMessage(tabs[0].id,
      { action: "start_autofill", llm: getLlm(), ...extraPayload },
      response => {
        if (chrome.runtime.lastError) {
          showStatus("autofillStatus", "error", "Could not connect to page. Try reloading the tab.");
          btn.disabled = false; btn.textContent = "Auto-Fill This Application";
          hideEl("progressWrap");
        }
      }
    );
  });
}

document.getElementById("autofillBtn").addEventListener("click", () => {
  startAutofillRun();
});

document.getElementById("fillApprovedBtn").addEventListener("click", () => {
  // Fill from the tracker's ready-to-apply package for THIS page: the exact versioned
  // resume artifact + approved answers. Never the generic profile, never /last-resume.
  const btn = document.getElementById("autofillBtn");
  if (btn) { btn.disabled = true; btn.textContent = "Filling..."; }
  fillLogEntries = [];
  hideEl("fillLog"); hideEl("autofillStatus"); showEl("progressWrap");
  document.getElementById("progressBar").style.width = "0%";
  document.getElementById("progressLabel").textContent = "Matching approved item…";
  chrome.tabs.query({ active: true, currentWindow: true }, tabs => {
    chrome.tabs.sendMessage(tabs[0].id, { action: "start_approved_fill" }, () => {
      if (chrome.runtime.lastError) {
        showStatus("autofillStatus", "error", "Could not connect to page. Try reloading the tab.");
        if (btn) { btn.disabled = false; btn.textContent = "Auto-Fill This Application"; }
        hideEl("progressWrap");
      }
    });
  });
});

document.getElementById("refreshApprovedBtn").addEventListener("click", () => {
  loadApprovedMatches(true);
});

function normalizeApprovedItem(raw) {
  if (!raw || typeof raw !== "object") return null;
  const item = { ...raw };
  item.id = item.id ?? item.match_id ?? item.queue_id ?? item.uuid ?? null;
  item.company = item.company ?? item.company_name ?? item.org ?? "";
  item.role = item.role ?? item.title ?? item.job_title ?? "";
  item.apply_url = item.apply_url ?? item.url ?? item.job_url ?? "";
  if (!item.tailored_data && typeof item.profile_override === "object") {
    item.tailored_data = item.profile_override;
  }
  if (!item.tailored_data && typeof item.tailored === "object") {
    item.tailored_data = item.tailored;
  }
  return item.id ? item : null;
}

function renderApprovedMatches() {
  const sel = document.getElementById("approvedMatchSelect");
  if (!sel) return;
  const existing = sel.value;
  sel.innerHTML = "";
  if (!approvedMatches.length) {
    const opt = document.createElement("option");
    opt.value = "";
    opt.textContent = "No approved items available";
    sel.appendChild(opt);
    return;
  }
  const placeholder = document.createElement("option");
  placeholder.value = "";
  placeholder.textContent = `Select approved item (${approvedMatches.length})`;
  sel.appendChild(placeholder);
  approvedMatches.forEach(item => {
    const opt = document.createElement("option");
    opt.value = String(item.id);
    const title = item.role || "Role";
    const company = item.company || "Company";
    opt.textContent = `${company} — ${title}`;
    sel.appendChild(opt);
  });
  if (existing && approvedMatches.some(item => String(item.id) === String(existing))) {
    sel.value = existing;
  }
}

async function loadApprovedMatches(showToast = false) {
  const apiUrl = getApiUrl();
  try {
    const res = await fetch(`${apiUrl}/matches/approved`);
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const payload = await res.json();
    const rows = Array.isArray(payload) ? payload : (Array.isArray(payload?.items) ? payload.items : []);
    approvedMatches = rows.map(normalizeApprovedItem).filter(Boolean);
    renderApprovedMatches();
    if (showToast) {
      showStatus("autofillStatus", "success", `Loaded ${approvedMatches.length} approved queue item(s).`);
    }
  } catch (e) {
    approvedMatches = [];
    renderApprovedMatches();
    if (showToast) {
      showStatus("autofillStatus", "warning", "Queue endpoint unavailable. Using regular profile fill.");
    }
    LHLog.warn("autofill", "Failed to load approved queue items", { err: e?.message });
  }
}

function handleFillProgress(data) {
  const progressBar = document.getElementById("progressBar");
  const progressLabel = document.getElementById("progressLabel");
  const btn = document.getElementById("autofillBtn");

  if (data.status === "done") {
    progressBar.style.width = "100%";
    progressBar.style.background = "#28a745";
    progressLabel.textContent = data.message;
    showStatus("autofillStatus", "success", `✓ ${data.message}`);
    if (fillLogEntries.length > 0) {
      const log = document.getElementById("fillLog");
      log.innerHTML = fillLogEntries.map(e => `<div class="entry">${e}</div>`).join("");
      showEl("fillLog");
    }
    // Show "Log Application" banner
    showEl("logAppBanner");
    btn.disabled = false; btn.textContent = "Auto-Fill Again";
  } else if (data.status === "error") {
    showStatus("autofillStatus", "error", "✗ " + data.message);
    hideEl("progressWrap");
    btn.disabled = false; btn.textContent = "Auto-Fill This Application";
  } else if (data.status === "warning") {
    showStatus("autofillStatus", "warning", "⚠ " + data.message);
  } else if (data.status === "filling") {
    const pct = data.total > 0 ? Math.round((data.filled / data.total) * 100) : 0;
    progressBar.style.width = pct + "%";
    progressLabel.textContent = data.message;
    fillLogEntries.push(data.message);
  } else {
    progressLabel.textContent = data.message;
    showStatus("autofillStatus", "loading", data.message, true);
  }
}

// ─────────────────────────────────────────────
// COVER LETTER TAB
// ─────────────────────────────────────────────
document.getElementById("coverBtn").addEventListener("click", () => {
  const btn = document.getElementById("coverBtn");
  const company = document.getElementById("companyInput").value.trim() || "the company";
  const role = document.getElementById("roleInput").value.trim() || "the role";

  btn.disabled = true; btn.textContent = "Generating...";
  hideEl("coverResult");
  showStatus("coverStatus", "loading", `Generating cover letter via ${getLlm() === "claude" ? "Claude" : "Ollama"}...`, true);

  scrapePageContext(async text => {
    try {
      const res = await fetch(`${getApiUrl()}/cover-letter`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ company, role, jd_text: text, llm: getLlm() })
      });
      const data = await res.json();
      if (!res.ok) throw new Error(data.detail || "Generation failed");
      document.getElementById("coverText").textContent = data.cover_letter || data.text || "";
      hideEl("coverStatus");
      showEl("coverResult");
    } catch (e) {
      showStatus("coverStatus", "error", "Error: " + e.message);
    } finally {
      btn.disabled = false; btn.textContent = "Generate Cover Letter";
    }
  });
});

document.getElementById("copyBtn").addEventListener("click", () => {
  const text = document.getElementById("coverText").textContent;
  navigator.clipboard.writeText(text).then(() => {
    const btn = document.getElementById("copyBtn");
    btn.textContent = "Copied ✓"; btn.classList.add("copied");
    setTimeout(() => { btn.textContent = "Copy"; btn.classList.remove("copied"); }, 2000);
  });
});

// ─────────────────────────────────────────────
// CHAT TAB
// ─────────────────────────────────────────────
async function loadFAQs() {
  if (!pageContext) return;
  const faqDiv = document.getElementById("faqList");
  try {
    const res = await fetch(`${getApiUrl()}/suggest-questions`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ jd_text: pageContext, llm: getLlm() })
    });
    const questions = await res.json();
    faqDiv.innerHTML = "";
    (Array.isArray(questions) ? questions : []).forEach(q => {
      const btn = document.createElement("button");
      btn.className = "faq-btn"; btn.textContent = "❓ " + q;
      btn.onclick = () => sendChat(q);
      faqDiv.appendChild(btn);
    });
  } catch (e) {
    faqDiv.textContent = "Could not load suggestions.";
  }
}

async function sendChat(msg = null) {
  const input = document.getElementById("chatInput");
  const question = msg || input.value.trim();
  if (!question) return;
  addBubble(question, "user");
  input.value = "";
  const historyDiv = document.getElementById("chat-history");
  historyDiv.scrollTop = historyDiv.scrollHeight;

  try {
    const res = await fetch(`${getApiUrl()}/chat`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ context: pageContext, question, history: chatHistory, llm: getLlm() })
    });
    const data = await res.json();
    addBubble(data.answer || "No response.", "bot");
    chatHistory.push({ role: "user", content: question });
    chatHistory.push({ role: "assistant", content: data.answer });
    saveSession({ chatHistory });
    historyDiv.scrollTop = historyDiv.scrollHeight;
  } catch (e) {
    addBubble("Error: could not reach backend.", "bot");
  }
}

function addBubble(text, type) {
  const div = document.createElement("div");
  div.className = `msg ${type}`; div.textContent = text;
  document.getElementById("chat-history").appendChild(div);
}

document.getElementById("sendChatBtn").addEventListener("click", () => sendChat());
document.getElementById("chatInput").addEventListener("keydown", e => { if (e.key === "Enter") sendChat(); });

// ─────────────────────────────────────────────
// REFRESH / SETTINGS
// ─────────────────────────────────────────────
document.getElementById("refreshBtn").addEventListener("click", () => {
  pageContext = null; analysisData = null; chatHistory = [];
  if (currentTabId) chrome.storage.session.remove(`tab_${currentTabId}`);
  hideEl("matchResult");
  document.getElementById("chat-history").innerHTML = '<div class="msg bot">Page refreshed. What would you like to know?</div>';
  scrapePageContext(() => loadFAQs());
  detectPlatform();
});

document.getElementById("settingsBtn").addEventListener("click", () => {
  const tabs = document.querySelectorAll(".tab");
  const autofillTab = Array.from(tabs).find(t => t.dataset.panel === "panel-autofill");
  if (autofillTab) autofillTab.click();
  setTimeout(() => document.getElementById("llmSelect").focus(), 100);
});

// ─────────────────────────────────────────────
// DASHBOARD BUTTON
// ─────────────────────────────────────────────
document.getElementById("dashboardBtn").addEventListener("click", () => {
  const apiUrl = getApiUrl();
  const dashUrl = apiUrl.replace(/\/$/, "") + "/dashboard";
  chrome.tabs.create({ url: dashUrl });
});

// ─────────────────────────────────────────────
// LOG APPLICATION BANNER (appears after autofill)
// ─────────────────────────────────────────────
document.getElementById("logAppBtn").addEventListener("click", async () => {
  const company = lastDetectedCompany || document.getElementById("companyInput")?.value?.trim() || "";
  const role    = lastDetectedRole    || document.getElementById("roleInput")?.value?.trim()    || "";
  const platform = lastDetectedPlatform || "";

  try {
    await fetch(`${getApiUrl()}/applications`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        company: company || "Unknown Company",
        role: role || "Unknown Role",
        platform,
        status: "Applied",
        date_applied: new Date().toISOString().split("T")[0],
        url: "",
        notes: ""
      })
    });
    hideEl("logAppBanner");
    // Flash success on button
    const btn = document.getElementById("logAppBtn");
    btn.textContent = "Logged ✓";
    btn.style.background = "#166534";
  } catch (e) {
    console.error("Log app failed:", e);
  }
});

document.getElementById("skipLogBtn").addEventListener("click", () => {
  hideEl("logAppBanner");
});
