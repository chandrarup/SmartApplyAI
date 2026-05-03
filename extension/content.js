// LocalHire Agent — Content Script v2.0
// Platform detection, field mapping, and auto-fill logic

// ─────────────────────────────────────────────
// FIELD LABEL PATTERN MATCHING
// ─────────────────────────────────────────────
const FIELD_PATTERNS = [
  { key: "first_name", patterns: [/^first\s*name/i, /given\s*name/i] },
  { key: "last_name", patterns: [/^last\s*name/i, /family\s*name/i, /surname/i] },
  { key: "full_name", patterns: [/^(full\s*)?name$/i, /^your\s*name/i, /^name\b/i, /applicant\s*name/i] },
  { key: "email", patterns: [/e[\s-]?mail/i] },
  { key: "phone", patterns: [/phone|mobile|cell(\s*number)?/i, /telephone/i] },
  { key: "address_line1", patterns: [/^address(\s*line\s*1)?$/i, /street\s*address/i, /mailing\s*address/i] },
  { key: "address_line2", patterns: [/address\s*line\s*2/i, /apt|suite|unit/i] },
  { key: "city", patterns: [/^city$/i, /^town$/i] },
  { key: "state", patterns: [/^state$/i, /^province$/i, /^state\s*\//i] },
  { key: "zip", patterns: [/zip|postal\s*code/i] },
  { key: "country", patterns: [/^country$/i] },
  { key: "linkedin", patterns: [/linkedin/i] },
  { key: "github", patterns: [/github/i] },
  { key: "website", patterns: [/website|portfolio|personal\s*url|personal\s*site/i] },
  { key: "salary", patterns: [/salary|compensation|pay\s*expect|desired\s*pay|wage/i] },
  { key: "years_experience", patterns: [/years?\s*(of\s*)?experience/i] },
  { key: "start_date", patterns: [/start\s*date|available.*date|earliest\s*start/i] },
  { key: "work_authorization", patterns: [/work\s*auth|legally\s*(authorized|eligible)|authorized\s*to\s*work/i] },
  { key: "requires_sponsorship", patterns: [/sponsor|visa\s*sponsor|require.*sponsor/i] },
  { key: "relocate", patterns: [/relocat/i, /willing\s*to\s*move/i] },
  { key: "gender", patterns: [/^gender$/i] },
  { key: "veteran", patterns: [/veteran/i, /military/i] },
  { key: "disability", patterns: [/disability|disabled/i] },
  { key: "ethnicity", patterns: [/ethnic|race|racial/i] },
  { key: "cover_letter", patterns: [/cover\s*letter/i] },
  { key: "summary", patterns: [/summary|tell\s*us\s*about|about\s*yourself|introduce\s*yourself|professional\s*summary|^background$/i] },
  { key: "current_company", patterns: [/current\s*(company|employer)/i, /present\s*employer/i] },
  { key: "current_title", patterns: [/current\s*(title|position|role)|present\s*title/i] },
  { key: "notice_period", patterns: [/notice\s*period|weeks?\s*notice/i] },
  { key: "referral", patterns: [/referral|how\s*did\s*you\s*hear|referred\s*by/i] }
];

function matchFieldKey(labelText) {
  if (!labelText) return null;
  const text = labelText.trim();
  for (const { key, patterns } of FIELD_PATTERNS) {
    if (patterns.some(p => p.test(text))) return key;
  }
  return null;
}

// ─────────────────────────────────────────────
// NATIVE INPUT SETTER (React/Angular/Vue compatible)
// ─────────────────────────────────────────────
function setNativeValue(element, value) {
  const proto = element.tagName === "TEXTAREA" ? window.HTMLTextAreaElement.prototype : window.HTMLInputElement.prototype;
  const setter = Object.getOwnPropertyDescriptor(proto, "value")?.set;
  if (setter) {
    setter.call(element, value);
  } else {
    element.value = value;
  }
  element.dispatchEvent(new Event("input", { bubbles: true }));
  element.dispatchEvent(new Event("change", { bubbles: true }));
  element.dispatchEvent(new Event("blur", { bubbles: true }));
}

function setSelectValue(element, value) {
  if (!value) return;
  const valLower = value.toString().toLowerCase();
  let bestOption = null;
  for (const opt of element.options) {
    const t = opt.text.toLowerCase(), v = opt.value.toLowerCase();
    if (t === valLower || v === valLower) { bestOption = opt; break; }
    if (!bestOption && (t.includes(valLower) || valLower.includes(t))) bestOption = opt;
  }
  if (bestOption) {
    element.value = bestOption.value;
    element.dispatchEvent(new Event("change", { bubbles: true }));
  }
}

function setRadioOrCheckbox(container, value) {
  if (!value || !container) return;
  const valLower = value.toString().toLowerCase();
  const inputs = container.querySelectorAll('input[type="radio"], input[type="checkbox"]');
  for (const input of inputs) {
    const lbl = getLabelForInput(input)?.toLowerCase() || "";
    const val = (input.value || "").toLowerCase();
    if (lbl.includes(valLower) || val.includes(valLower) || valLower.includes(val)) {
      input.checked = true;
      input.dispatchEvent(new Event("change", { bubbles: true }));
      break;
    }
  }
}

// ─────────────────────────────────────────────
// LABEL EXTRACTION
// ─────────────────────────────────────────────
function getLabelForInput(input) {
  if (input.getAttribute("aria-label")) return input.getAttribute("aria-label");
  if (input.id) {
    const lbl = document.querySelector(`label[for="${CSS.escape(input.id)}"]`);
    if (lbl) return lbl.innerText.trim();
  }
  const pLabel = input.closest("label");
  if (pLabel) return pLabel.innerText.trim();
  let prev = input.previousElementSibling;
  while (prev) {
    const tag = prev.tagName;
    if (tag === "LABEL" || prev.classList.contains("label") || prev.getAttribute("role") === "label") {
      return prev.innerText.trim();
    }
    prev = prev.previousElementSibling;
  }
  const container = input.closest(".form-group, .field, .question, .form-field, [data-field]");
  if (container) {
    const lbl = container.querySelector("label, .label, .field-label, legend, .question-label");
    if (lbl) return lbl.innerText.trim();
  }
  return input.placeholder || input.name || input.id || "";
}

// ─────────────────────────────────────────────
// PLATFORM DETECTORS & FIELD SCANNERS
// ─────────────────────────────────────────────
function detectPlatform() {
  const h = location.hostname;
  if (/myworkdayjobs\.com|workday\.com/.test(h)) return "workday";
  if (/greenhouse\.io/.test(h)) return "greenhouse";
  if (/lever\.co/.test(h)) return "lever";
  if (/bamboohr\.com/.test(h)) return "bamboohr";
  if (/icims\.com/.test(h)) return "icims";
  if (/smartrecruiters\.com/.test(h)) return "smartrecruiters";
  if (/linkedin\.com/.test(h) && document.querySelector('.jobs-easy-apply-modal, [data-test-modal-id="easy-apply-modal"]')) return "linkedin";
  if (/taleo\.net/.test(h)) return "taleo";
  return "generic";
}

function getPlatformName(key) {
  const names = { workday: "Workday", greenhouse: "Greenhouse", lever: "Lever", bamboohr: "BambooHR", icims: "iCIMS", smartrecruiters: "SmartRecruiters", linkedin: "LinkedIn Easy Apply", taleo: "Taleo", generic: "Generic" };
  return names[key] || "Unknown";
}

function getFormFields(platform) {
  let inputs = [];
  if (platform === "workday") {
    inputs = document.querySelectorAll('[data-automation-id] input:not([type="hidden"]):not([type="submit"]), [data-automation-id] textarea, [data-automation-id] select');
  } else if (platform === "linkedin") {
    const modal = document.querySelector('.jobs-easy-apply-modal, [data-test-modal-id="easy-apply-modal"]');
    inputs = modal ? modal.querySelectorAll('input:not([type="hidden"]):not([type="submit"]):not([type="file"]), textarea, select') : [];
  } else {
    inputs = document.querySelectorAll('input:not([type="hidden"]):not([type="submit"]):not([type="button"]):not([type="file"]):not([type="image"]), textarea, select');
  }

  const fields = [];
  inputs.forEach(el => {
    if (el.type === "radio" || el.type === "checkbox") return; // handled separately
    const label = getLabelForInput(el);
    if (!label && !el.name) return;
    fields.push({
      element: el,
      label: label || el.name || el.id || "",
      type: el.type || el.tagName.toLowerCase(),
      name: el.name || el.id || "",
      options: el.tagName === "SELECT" ? Array.from(el.options).map(o => o.text) : []
    });
  });
  return fields;
}

function fillField(platform, field, value) {
  if (!value || value === "SKIP" || !field.element) return false;
  const el = field.element;
  try {
    if (el.tagName === "SELECT") {
      setSelectValue(el, value);
    } else {
      el.focus();
      setNativeValue(el, String(value));
    }
    return true;
  } catch (e) {
    return false;
  }
}

// ─────────────────────────────────────────────
// CLEAN PAGE TEXT (for JD extraction)
// ─────────────────────────────────────────────
function getCleanText() {
  const clone = document.body.cloneNode(true);
  ["script", "style", "noscript", "iframe", "svg", "nav", "header", "footer", '[role="navigation"]', ".nav", ".footer", ".header", ".ads"].forEach(sel => {
    clone.querySelectorAll(sel).forEach(el => el.remove());
  });
  let main = clone.querySelector("main") || clone.querySelector("article");
  if (!main) {
    const candidates = clone.querySelectorAll('div[class*="job"], div[class*="description"], div[id*="job"]');
    let maxLen = 0;
    candidates.forEach(div => { if (div.innerText.length > maxLen) { maxLen = div.innerText.length; main = div; } });
  }
  return (main || clone).innerText.replace(/\s+/g, " ").trim();
}

// ─────────────────────────────────────────────
// API URL HELPER
// ─────────────────────────────────────────────
async function getApiUrl() {
  return new Promise(resolve => {
    chrome.runtime.sendMessage({ action: "get_api_url" }, res => {
      resolve((res && res.url) ? res.url : "http://127.0.0.1:8000");
    });
  });
}

function delay(ms) { return new Promise(r => setTimeout(r, ms)); }

function extractCompanyFromPage() {
  const og = document.querySelector('meta[property="og:site_name"]');
  if (og && og.content) return og.content;
  const title = document.title;
  const match = title.match(/(?:at|@)\s+(.+?)(?:\s*[-–|]|$)/i);
  if (match) return match[1].trim();
  return title.split(/[-–|]/)[0].trim();
}

// ─────────────────────────────────────────────
// MAIN AUTOFILL ORCHESTRATOR
// ─────────────────────────────────────────────
async function runAutoFill() {
  const platform = detectPlatform();
  const platformName = getPlatformName(platform);

  sendProgress({ status: "detecting", message: `Detected: ${platformName}` });
  await delay(200);

  const fields = getFormFields(platform);
  if (fields.length === 0) {
    sendProgress({ status: "error", message: "No fillable form fields found on this page." });
    return;
  }

  sendProgress({ status: "scanning", message: `Found ${fields.length} form fields on ${platformName}` });

  const jdText = getCleanText();
  const company = extractCompanyFromPage();
  const apiUrl = await getApiUrl();

  const fieldDescriptors = fields.map((f, i) => ({
    index: i,
    label: f.label,
    type: f.type,
    name: f.name,
    options: f.options.slice(0, 20)
  }));

  sendProgress({ status: "thinking", message: "AI is generating answers from your profile..." });

  let answers = {};
  try {
    const res = await fetch(`${apiUrl}/autofill`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ fields: fieldDescriptors, jd_text: jdText, company })
    });
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    answers = await res.json();
  } catch (err) {
    sendProgress({ status: "warning", message: `Backend unavailable (${err.message}). Using profile fallback.` });
    answers = await getLocalAnswers(fieldDescriptors);
  }

  let filled = 0, skipped = 0;
  for (let i = 0; i < fields.length; i++) {
    const field = fields[i];
    const answer = answers[field.label] || answers[field.name] || answers[String(i)] || answers[i];
    if (answer && answer !== "SKIP") {
      await delay(100);
      const ok = fillField(platform, field, answer);
      if (ok) {
        filled++;
        sendProgress({ status: "filling", message: `✓ ${field.label}`, filled, total: fields.length });
      } else {
        skipped++;
      }
    } else {
      skipped++;
    }
  }

  sendProgress({ status: "done", message: `Done! Filled ${filled} of ${fields.length} fields`, filled, skipped, total: fields.length });
}

async function getLocalAnswers(fieldDescriptors) {
  return new Promise(resolve => {
    chrome.storage.local.get(["autofill_profile"], result => {
      const profile = result.autofill_profile || {};
      const answers = {};
      for (const f of fieldDescriptors) {
        const key = matchFieldKey(f.label);
        if (key && profile[key]) answers[f.label] = profile[key];
      }
      resolve(answers);
    });
  });
}

function sendProgress(data) {
  try {
    chrome.runtime.sendMessage({ action: "autofill_progress", data });
  } catch (e) { /* popup may be closed */ }
}

// ─────────────────────────────────────────────
// MESSAGE LISTENER
// ─────────────────────────────────────────────
chrome.runtime.onMessage.addListener((message, sender, sendResponse) => {
  if (message.action === "get_text") {
    sendResponse({ text: getCleanText() });
    return true;
  }
  if (message.action === "get_platform") {
    const key = detectPlatform();
    sendResponse({ platform: getPlatformName(key), key });
    return true;
  }
  if (message.action === "start_autofill") {
    runAutoFill().catch(err => sendProgress({ status: "error", message: err.message }));
    sendResponse({ started: true });
    return true;
  }
  return true;
});
