// LocalHire Agent — Popup Script v2.0

// ─────────────────────────────────────────────
// STATE
// ─────────────────────────────────────────────
let pageContext = null;
let analysisData = null;
let chatHistory = [];
let fillLogEntries = [];

// ─────────────────────────────────────────────
// INIT
// ─────────────────────────────────────────────
document.addEventListener("DOMContentLoaded", async () => {
  loadApiUrl();
  detectPlatform();
  scrapePageContext(() => {
    loadFAQs();
  });

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
  return document.getElementById("apiUrlInput").value.trim() || "http://127.0.0.1:8000";
}

function loadApiUrl() {
  chrome.runtime.sendMessage({ action: "get_api_url" }, res => {
    if (res?.url) document.getElementById("apiUrlInput").value = res.url;
  });
}

function scrapePageContext(callback) {
  if (pageContext) { if (callback) callback(pageContext); return; }
  chrome.tabs.query({ active: true, currentWindow: true }, tabs => {
    chrome.tabs.sendMessage(tabs[0].id, { action: "get_text" }, response => {
      if (chrome.runtime.lastError || !response) return;
      pageContext = response.text;
      if (callback) callback(pageContext);
    });
  });
}

function detectPlatform() {
  chrome.tabs.query({ active: true, currentWindow: true }, tabs => {
    chrome.tabs.sendMessage(tabs[0].id, { action: "get_platform" }, response => {
      if (chrome.runtime.lastError || !response) return;
      document.getElementById("platformBadge").textContent = response.platform;
      // Auto-detect company for cover letter
      const url = tabs[0].url || "";
      const title = tabs[0].title || "";
      const companyMatch = title.match(/(?:at|@)\s+(.+?)(?:\s*[-–|]|$)/i);
      if (companyMatch) document.getElementById("companyInput").value = companyMatch[1].trim();
    });
  });
}

// ─────────────────────────────────────────────
// MATCH TAB
// ─────────────────────────────────────────────
document.getElementById("analyzeBtn").addEventListener("click", () => {
  const btn = document.getElementById("analyzeBtn");
  btn.disabled = true;
  btn.textContent = "Analyzing...";
  hideEl("matchResult");
  showStatus("matchStatus", "loading", "Reading page & thinking...", true);

  scrapePageContext(async text => {
    try {
      const res = await fetch(`${getApiUrl()}/analyze`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ jd_text: text })
      });
      const data = await res.json();
      analysisData = data;

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

      // Auto-fill role for cover letter
      if (data.role) document.getElementById("roleInput").value = data.role;

      hideEl("matchStatus");
      showEl("matchResult");
    } catch (e) {
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
      btn.textContent = "PDF Failed"; btn.disabled = false;
    }
  } catch (e) {
    btn.textContent = "Error: " + e.message; btn.disabled = false;
  }
});

// ─────────────────────────────────────────────
// AUTOFILL TAB
// ─────────────────────────────────────────────
document.getElementById("autofillBtn").addEventListener("click", () => {
  const btn = document.getElementById("autofillBtn");
  btn.disabled = true; btn.textContent = "Filling...";
  fillLogEntries = [];
  hideEl("fillLog");
  hideEl("autofillStatus");
  showEl("progressWrap");
  document.getElementById("progressBar").style.width = "0%";
  document.getElementById("progressLabel").textContent = "Starting...";

  chrome.tabs.query({ active: true, currentWindow: true }, tabs => {
    chrome.tabs.sendMessage(tabs[0].id, { action: "start_autofill" }, response => {
      if (chrome.runtime.lastError) {
        showStatus("autofillStatus", "error", "Could not connect to page. Try reloading the tab.");
        btn.disabled = false; btn.textContent = "Auto-Fill This Application";
        hideEl("progressWrap");
      }
    });
  });
});

function handleFillProgress(data) {
  const statusEl = document.getElementById("autofillStatus");
  const progressBar = document.getElementById("progressBar");
  const progressLabel = document.getElementById("progressLabel");
  const btn = document.getElementById("autofillBtn");

  if (data.status === "done") {
    showEl("progressWrap");
    progressBar.style.width = "100%";
    progressBar.style.background = "#28a745";
    progressLabel.textContent = data.message;
    showStatus("autofillStatus", "success", `✓ ${data.message}`);
    if (fillLogEntries.length > 0) {
      const log = document.getElementById("fillLog");
      log.innerHTML = fillLogEntries.map(e => `<div class="entry">${e}</div>`).join("");
      showEl("fillLog");
    }
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

document.getElementById("saveApiBtn").addEventListener("click", () => {
  const url = document.getElementById("apiUrlInput").value.trim();
  if (!url) return;
  chrome.runtime.sendMessage({ action: "save_api_url", url }, () => {
    const btn = document.getElementById("saveApiBtn");
    btn.textContent = "Saved ✓"; btn.style.background = "#dcfce7"; btn.style.color = "#166534";
    setTimeout(() => { btn.textContent = "Save API URL"; btn.style = ""; }, 2000);
  });
});

// ─────────────────────────────────────────────
// COVER LETTER TAB
// ─────────────────────────────────────────────
document.getElementById("coverBtn").addEventListener("click", () => {
  const btn = document.getElementById("coverBtn");
  const company = document.getElementById("companyInput").value.trim() || "the company";
  const role = document.getElementById("roleInput").value.trim() || "the role";

  btn.disabled = true; btn.textContent = "Generating...";
  hideEl("coverResult");
  showStatus("coverStatus", "loading", "Writing your cover letter...", true);

  scrapePageContext(async text => {
    try {
      const res = await fetch(`${getApiUrl()}/cover-letter`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ company, role, jd_text: text })
      });
      const data = await res.json();
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
      body: JSON.stringify({ jd_text: pageContext })
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
      body: JSON.stringify({ context: pageContext, question, history: chatHistory })
    });
    const data = await res.json();
    addBubble(data.answer || "No response.", "bot");
    chatHistory.push({ role: "user", content: question });
    chatHistory.push({ role: "assistant", content: data.answer });
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
  document.getElementById("chat-history").innerHTML = '<div class="msg bot">Page refreshed. What would you like to know?</div>';
  scrapePageContext(() => loadFAQs());
  detectPlatform();
});

document.getElementById("settingsBtn").addEventListener("click", () => {
  const tabs = document.querySelectorAll(".tab");
  const autofillTab = Array.from(tabs).find(t => t.dataset.panel === "panel-autofill");
  if (autofillTab) autofillTab.click();
  document.getElementById("apiUrlInput").focus();
});
