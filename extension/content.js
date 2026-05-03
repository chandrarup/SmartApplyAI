// LocalHire Agent — Content Script v2.2
// Platform detection, field mapping, and auto-fill logic for 8 ATS platforms

// ─────────────────────────────────────────────
// FIELD LABEL PATTERN MATCHING
// ─────────────────────────────────────────────
const FIELD_PATTERNS = [
  // Identity — more specific FIRST
  { key: "first_name",  patterns: [/^first\s*name/i, /^first$/i, /given\s*name/i, /forename/i] },
  { key: "last_name",   patterns: [/^last\s*name/i, /^last$/i, /family\s*name/i, /surname/i] },
  { key: "full_name",   patterns: [/^(full\s*)?name$/i, /^your\s*name/i, /applicant\s*name/i] },
  // Contact
  { key: "email",       patterns: [/e[\s-]?mail/i] },
  { key: "phone",       patterns: [/phone|mobile\s*phone|cell(\s*number)?|telephone/i] },
  // Location — city MUST be before state (avoid "City, State, Zip" matching state)
  { key: "city",        patterns: [/^city$/i, /^town$/i, /location.*city/i, /city.*state/i, /candidate.?location/i] },
  { key: "state",       patterns: [/^state$/i, /^province$/i, /^state\s*[\/\\]?\s*province$/i] },
  { key: "zip",         patterns: [/zip|postal\s*code/i] },
  { key: "country",     patterns: [/^country$/i] },
  // Professional links
  { key: "linkedin",    patterns: [/linkedin/i] },
  { key: "github",      patterns: [/github/i] },
  { key: "website",     patterns: [/website|portfolio|personal\s*url|personal\s*site/i] },
  // Work eligibility — MUST be before salary/state to avoid false matches
  { key: "work_authorization", patterns: [
    /work\s*auth/i, /legally\s*(authorized|eligible)/i,
    /authorized\s*to\s*work/i, /eligible\s*to\s*work/i,
    /currently\s*eligible\s*to\s*work/i,
    /authorized.*without.*sponsor/i,
  ]},
  { key: "requires_sponsorship", patterns: [
    /require.*sponsor/i, /visa\s*sponsor/i, /need.*sponsor/i,
    /now\s*or.*future.*sponsor/i, /future.*require.*sponsor/i,
    /\bsponsor\b/i,
  ]},
  // Compensation & schedule
  { key: "salary",           patterns: [/salary|compensation|pay\s*expect|desired\s*pay|wage|expected\s*salary/i] },
  { key: "years_experience", patterns: [/years?\s*(of\s*)?experience/i, /experience.*years/i] },
  { key: "start_date",       patterns: [/start\s*date|available.*date|earliest\s*start|when.*available/i] },
  { key: "relocate",         patterns: [/relocat/i, /willing\s*to\s*move/i] },
  { key: "notice_period",    patterns: [/notice\s*period|weeks?\s*notice/i] },
  // Address
  { key: "address_line1", patterns: [/^address(\s*line\s*1)?$/i, /street\s*address/i] },
  // Current employment
  { key: "current_company", patterns: [/current\s*(company|employer|organization)/i, /present\s*employer/i] },
  { key: "current_title",   patterns: [/current\s*(job\s*)?(title|position|role)/i, /present\s*title/i, /job\s*title/i] },
  // EEO
  { key: "gender",     patterns: [/^gender$/i, /gender\s*identity/i, /identify.*gender/i, /i\s*identify\s*my\s*gender/i] },
  { key: "veteran",    patterns: [/veteran/i, /military\s*status/i] },
  { key: "disability", patterns: [/disability|disabled/i] },
  { key: "ethnicity",  patterns: [/ethnic|race\b|racial/i, /identify.*ethnicity/i, /i\s*identify\s*my\s*ethnicity/i] },
  // Open-ended
  { key: "summary",      patterns: [/summary|tell\s*us\s*about|about\s*yourself|introduce\s*yourself|professional\s*summary|^background$/i] },
  { key: "cover_letter", patterns: [/cover\s*letter/i] },
  // Referral
  { key: "referral", patterns: [/referral|how\s*did\s*you\s*(hear|find|learn|know)|referred\s*by|source\s*of\s*hire/i] },
  { key: "pronouns", patterns: [/pronouns/i] },
];

// ─────────────────────────────────────────────
// LEVER NAME-ATTRIBUTE → LABEL MAP
// Real Lever fields use name= not labels
// ─────────────────────────────────────────────
const LEVER_NAME_LABELS = {
  "name":                "Full Name",
  "email":               "Email",
  "phone":               "Phone",
  "org":                 "Current Company",
  "urls[LinkedIn]":      "LinkedIn URL",
  "urls[GitHub]":        "GitHub URL",
  "urls[Portfolio]":     "Portfolio",
  "urls[Other]":         "Website",
  "comments":            "Tell us about yourself",
};

// ─────────────────────────────────────────────
// BAMBOOHR camelCase id → readable label map
// ─────────────────────────────────────────────
const BAMBOOHR_ID_LABELS = {
  "firstName":          "First Name",
  "lastName":           "Last Name",
  "email":              "Email Address",
  "phoneNumber":        "Phone Number",
  "city":               "City",
  "state":              "State / Province",
  "zip":                "Zip Code",
  "country":            "Country",
  "linkedin":           "LinkedIn Profile",
  "websitePortfolio":   "Website or Portfolio",
  "currentEmployer":    "Current Company",
  "currentJobTitle":    "Current Job Title",
  "workAuthorization":  "Are you authorized to work in the US?",
  "requireSponsorship": "Do you require sponsorship?",
  "yearsExperience":    "Years of Relevant Experience",
  "desiredSalary":      "Desired Salary",
  "howDidYouHearAboutUs": "How did you hear about this position?",
  "coverLetter":        "Tell us about yourself",
};

// ─────────────────────────────────────────────
// TALEO ftl-prefix id → readable label map
// ─────────────────────────────────────────────
const TALEO_ID_LABELS = {
  "ftlFirstName":      "First Name",
  "ftlLastName":       "Last Name",
  "ftlEmail":          "Email Address",
  "ftlPhone":          "Phone Number",
  "ftlCity":           "City",
  "ftlState":          "State / Province",
  "ftlCountry":        "Country",
  "ftlCurrentEmployer": "Current Company",
  "ftlCurrentTitle":   "Current Job Title",
  "ftlLinkedIn":       "LinkedIn URL",
  "ftlYearsExp":       "Years of Experience",
  "ftlSalary":         "Desired Salary",
  "ftlWorkAuth":       "Are you authorized to work in the US?",
  "ftlSponsorship":    "Do you require sponsorship?",
  "ftlGender":         "Gender",
  "ftlVeteran":        "Veteran Status",
  "ftlSummary":        "Tell us about yourself",
};

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
  const proto = element.tagName === "TEXTAREA"
    ? window.HTMLTextAreaElement.prototype
    : window.HTMLInputElement.prototype;
  const setter = Object.getOwnPropertyDescriptor(proto, "value")?.set;
  if (setter) setter.call(element, value);
  else element.value = value;
  element.dispatchEvent(new Event("input",  { bubbles: true }));
  element.dispatchEvent(new Event("change", { bubbles: true }));
  element.dispatchEvent(new Event("blur",   { bubbles: true }));
}

function setSelectValue(element, value) {
  if (!value) return false;
  const valLower = value.toString().toLowerCase().trim();
  let bestOption = null, partialMatch = null;
  for (const opt of element.options) {
    const t = opt.text.toLowerCase().trim(), v = opt.value.toLowerCase().trim();
    if (t === valLower || v === valLower) { bestOption = opt; break; }
    if (!partialMatch && (t.includes(valLower) || valLower.includes(t) || v.includes(valLower))) {
      partialMatch = opt;
    }
  }
  const chosen = bestOption || partialMatch;
  if (chosen) {
    element.value = chosen.value;
    element.dispatchEvent(new Event("change", { bubbles: true }));
    return true;
  }
  return false;
}

function setRadioGroup(container, value) {
  if (!value || !container) return false;
  const valLower = value.toString().toLowerCase();
  const inputs = container.querySelectorAll('input[type="radio"]');
  for (const input of inputs) {
    const lbl = getLabelForInput(input)?.toLowerCase() || "";
    const val = (input.value || "").toLowerCase();
    if (lbl.includes(valLower) || val === valLower || (valLower.includes("yes") && val === "yes") || (valLower.includes("no") && val === "no")) {
      input.checked = true;
      input.dispatchEvent(new Event("change", { bubbles: true }));
      return true;
    }
  }
  return false;
}

// ─────────────────────────────────────────────
// LABEL EXTRACTION — platform-aware
// ─────────────────────────────────────────────
function getLabelForInput(input, platform) {
  // 1. aria-label (LinkedIn, SmartRecruiters, BambooHR)
  const ariaLabel = input.getAttribute("aria-label");
  if (ariaLabel && ariaLabel.trim()) return ariaLabel.trim();

  // 2. Lever: use name-to-label map
  if (platform === "lever") {
    const name = input.name || "";
    if (LEVER_NAME_LABELS[name]) return LEVER_NAME_LABELS[name];
    // Custom question cards: name=cards[work_authorization][field0]
    const cardMatch = name.match(/cards\[([^\]]+)\]/);
    if (cardMatch) {
      // Fall back to nearby label
      const lbl = input.closest(".application-field, .form-field, .field, div")?.querySelector("label");
      if (lbl) return lbl.innerText.replace(/[\s*]+$/, "").trim();
    }
  }

  // 3. BambooHR: camelCase id map
  if (platform === "bamboohr") {
    if (BAMBOOHR_ID_LABELS[input.id]) return BAMBOOHR_ID_LABELS[input.id];
  }

  // 4. Taleo: ftl-prefixed id map
  if (platform === "taleo") {
    if (TALEO_ID_LABELS[input.id]) return TALEO_ID_LABELS[input.id];
    // Also check if name attr starts with a capital (Taleo uses FirstName, LastName)
    if (input.name) {
      const readable = input.name.replace(/([A-Z])/g, " $1").trim();
      if (readable) return readable;
    }
  }

  // 5. label[for=id]
  if (input.id) {
    const lbl = document.querySelector(`label[for="${CSS.escape(input.id)}"]`);
    if (lbl) return lbl.innerText.replace(/[\s*✱]+$/, "").trim();
  }

  // 6. Closest wrapping label
  const pLabel = input.closest("label");
  if (pLabel) return pLabel.innerText.replace(/[\s*]+$/, "").trim();

  // 7. iCIMS: look in iCIMS_Label td sibling
  const icimsRow = input.closest("tr");
  if (icimsRow) {
    const lblCell = icimsRow.querySelector("td.iCIMS_Label label, td label");
    if (lblCell) return lblCell.innerText.replace(/[\s*]+$/, "").trim();
  }

  // 8. Previous sibling label
  let prev = input.previousElementSibling;
  while (prev) {
    const tag = prev.tagName;
    if (tag === "LABEL" || prev.classList.contains("label") || prev.getAttribute("role") === "label") {
      return prev.innerText.replace(/[\s*]+$/, "").trim();
    }
    prev = prev.previousElementSibling;
  }

  // 9. Container label (generic form-group, iCIMS sections, etc.)
  const container = input.closest(".form-group, .field, .question, .form-field, [data-field], .fab-field, .application-field, .sr-field, .li-field, .wd-field");
  if (container) {
    const lbl = container.querySelector("label, .label, .field-label, legend, .question-label");
    if (lbl) return lbl.innerText.replace(/[\s*✱]+$/, "").trim();
  }

  // 10. Placeholder or name as last resort
  return input.placeholder || input.name || input.id || "";
}

// ─────────────────────────────────────────────
// PLATFORM DETECTORS
// ─────────────────────────────────────────────
function detectPlatform() {
  const h = location.hostname;
  const path = location.pathname;
  if (/myworkdayjobs\.com|workday\.com/.test(h)) return "workday";
  if (/greenhouse\.io/.test(h)) return "greenhouse";
  if (/lever\.co/.test(h) && /\/apply/.test(path)) return "lever";
  if (/lever\.co/.test(h)) return "lever";
  if (/bamboohr\.com/.test(h)) return "bamboohr";
  if (/icims\.com/.test(h)) return "icims";
  if (/smartrecruiters\.com/.test(h)) return "smartrecruiters";
  if (/linkedin\.com/.test(h) && document.querySelector('.jobs-easy-apply-modal, [data-test-modal-id="easy-apply-modal"]')) return "linkedin";
  if (/taleo\.net/.test(h)) return "taleo";
  if (/successfactors\.(com|eu)/.test(h)) return "successfactors";
  if (/jobvite\.com/.test(h)) return "jobvite";
  // Test pages
  if (/test\/lever/.test(path)) return "lever";
  if (/test\/bamboohr/.test(path)) return "bamboohr";
  if (/test\/icims/.test(path)) return "icims";
  if (/test\/smartrecruiters/.test(path)) return "smartrecruiters";
  if (/test\/linkedin/.test(path)) return "linkedin";
  if (/test\/taleo/.test(path)) return "taleo";
  if (/test\/greenhouse/.test(path)) return "greenhouse";
  if (/test\/workday/.test(path)) return "workday";
  return "generic";
}

function getPlatformName(key) {
  const names = {
    workday: "Workday", greenhouse: "Greenhouse", lever: "Lever",
    bamboohr: "BambooHR", icims: "iCIMS", smartrecruiters: "SmartRecruiters",
    linkedin: "LinkedIn Easy Apply", taleo: "Taleo",
    successfactors: "SAP SuccessFactors", jobvite: "Jobvite", generic: "Generic",
  };
  return names[key] || "Unknown";
}

// ─────────────────────────────────────────────
// PLATFORM-SPECIFIC FIELD SCANNERS
// ─────────────────────────────────────────────
function getFormFields(platform) {
  let rawInputs = [];

  if (platform === "workday") {
    // Workday: inputs inside [data-automation-id] wrappers
    rawInputs = Array.from(document.querySelectorAll(
      '[data-automation-id] input:not([type="hidden"]):not([type="submit"]):not([type="file"]),' +
      '[data-automation-id] textarea,' +
      '[data-automation-id] select'
    ));
  } else if (platform === "linkedin") {
    // LinkedIn: everything inside the Easy Apply modal
    const modal = document.querySelector('.jobs-easy-apply-modal, [data-test-modal-id="easy-apply-modal"]');
    if (modal) {
      rawInputs = Array.from(modal.querySelectorAll(
        'input:not([type="hidden"]):not([type="submit"]):not([type="file"]):not([type="checkbox"]),' +
        'textarea, select'
      ));
    }
  } else if (platform === "icims") {
    // iCIMS: elements with class iCIMS_Input
    rawInputs = Array.from(document.querySelectorAll(
      '.iCIMS_Input, input[name*="applicant"], select[name*="applicant"], textarea[name*="applicant"]'
    ));
  } else if (platform === "smartrecruiters") {
    // SmartRecruiters: data-test-id attributes or generic
    rawInputs = Array.from(document.querySelectorAll(
      'input[data-test-id]:not([type="hidden"]):not([type="file"]),' +
      'select[data-test-id], textarea[data-test-id],' +
      'input:not([type="hidden"]):not([type="submit"]):not([type="file"]):not([type="checkbox"]),' +
      'textarea, select'
    ));
    // Deduplicate
    rawInputs = [...new Set(rawInputs)];
  } else if (platform === "taleo") {
    // Taleo: ftlField class or ftl-prefixed IDs
    rawInputs = Array.from(document.querySelectorAll(
      'input.ftlField:not([type="hidden"]):not([type="submit"]),' +
      'select.ftlField, textarea.ftlField,' +
      'input[id^="ftl"]:not([type="hidden"]),' +
      'select[id^="ftl"], textarea[id^="ftl"]'
    ));
    rawInputs = [...new Set(rawInputs)];
  } else if (platform === "greenhouse") {
    // Greenhouse: standard inputs, exclude resume/file uploads
    rawInputs = Array.from(document.querySelectorAll(
      'input:not([type="hidden"]):not([type="submit"]):not([type="file"]):not([type="checkbox"]):not([type="radio"]),' +
      'textarea, select'
    ));
  } else {
    // Generic, Lever, BambooHR, and everything else
    rawInputs = Array.from(document.querySelectorAll(
      'input:not([type="hidden"]):not([type="submit"]):not([type="button"]):not([type="file"]):not([type="image"]),' +
      'textarea, select'
    ));
  }

  // Build field descriptor array
  const fields = [];
  const seen = new Set();
  for (const el of rawInputs) {
    // Skip radio/checkbox here (handled separately via setRadioGroup)
    if (el.type === "radio" || el.type === "checkbox") continue;
    const label = getLabelForInput(el, platform);
    const identity = label || el.name || el.id;
    if (!identity || seen.has(identity)) continue;
    seen.add(identity);
    fields.push({
      element: el,
      label: label || el.name || el.id || "",
      type: el.type || el.tagName.toLowerCase(),
      name: el.name || el.id || "",
      options: el.tagName === "SELECT"
        ? Array.from(el.options).map(o => o.text.trim()).filter(t => t && t !== "--" && !t.startsWith("--"))
        : [],
    });
  }
  return fields;
}

// Also scan radio button groups separately
function getRadioGroups(platform) {
  const groups = {};
  let scope = document;
  if (platform === "linkedin") {
    scope = document.querySelector('.jobs-easy-apply-modal, [data-test-modal-id="easy-apply-modal"]') || document;
  }
  const radios = scope.querySelectorAll('input[type="radio"]');
  for (const r of radios) {
    const name = r.name;
    if (!name) continue;
    if (!groups[name]) {
      const container = r.closest(".field, .form-group, .application-field, .fab-field, .sr-field, fieldset, .wd-field, tr, .li-field");
      const label = container ? (getLabelForInput(r, platform) || container.querySelector("label")?.innerText?.trim()) : (r.name);
      groups[name] = { container, label: label || name, inputs: [] };
    }
    groups[name].inputs.push(r);
  }
  return groups;
}

// ─────────────────────────────────────────────
// FILL FIELD
// ─────────────────────────────────────────────
function fillField(platform, field, value) {
  if (!value || value === "SKIP" || !field.element) return false;
  const el = field.element;
  try {
    if (el.tagName === "SELECT") return setSelectValue(el, value);
    el.focus();
    setNativeValue(el, String(value));
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
  ["script","style","noscript","iframe","svg","nav","header","footer",
   '[role="navigation"]',".nav",".footer",".header"].forEach(sel => {
    clone.querySelectorAll(sel).forEach(el => el.remove());
  });
  let main = clone.querySelector("main") || clone.querySelector("article");
  if (!main) {
    let maxLen = 0;
    clone.querySelectorAll('div[class*="job"], div[class*="description"], div[id*="job"]').forEach(div => {
      if (div.innerText?.length > maxLen) { maxLen = div.innerText.length; main = div; }
    });
  }
  return (main || clone).innerText.replace(/\s+/g, " ").trim();
}

// ─────────────────────────────────────────────
// SETTINGS HELPERS
// ─────────────────────────────────────────────
async function getSettings() {
  return new Promise(resolve => {
    chrome.runtime.sendMessage({ action: "get_settings" }, res => {
      resolve({
        apiUrl: (res && res.url) ? res.url : "http://127.0.0.1:8000",
        llm: (res && res.llm) ? res.llm : "ollama",
      });
    });
  });
}

function delay(ms) { return new Promise(r => setTimeout(r, ms)); }

function extractCompanyFromPage() {
  const og = document.querySelector('meta[property="og:site_name"]');
  if (og?.content) return og.content;
  const title = document.title;
  const match = title.match(/(?:at|@)\s+(.+?)(?:\s*[-–|]|$)/i);
  if (match) return match[1].trim();
  return title.split(/[-–|]/)[0].trim();
}

// ─────────────────────────────────────────────
// MAIN AUTOFILL ORCHESTRATOR
// ─────────────────────────────────────────────
async function runAutoFill(preferredLlm) {
  const platform = detectPlatform();
  const platformName = getPlatformName(platform);

  sendProgress({ status: "detecting", message: `Detected: ${platformName}` });
  await delay(200);

  const fields = getFormFields(platform);
  const radioGroups = getRadioGroups(platform);

  if (fields.length === 0 && Object.keys(radioGroups).length === 0) {
    sendProgress({ status: "error", message: "No fillable form fields found on this page." });
    return;
  }

  const totalFields = fields.length + Object.keys(radioGroups).length;
  sendProgress({ status: "scanning", message: `Found ${totalFields} fields on ${platformName}` });

  const jdText = getCleanText();
  const company = extractCompanyFromPage();
  const { apiUrl, llm } = await getSettings();
  const activeLlm = preferredLlm || llm;

  // Build descriptors for API
  const fieldDescriptors = fields.map((f, i) => ({
    index: i,
    label: f.label,
    type: f.type,
    name: f.name,
    options: f.options.slice(0, 20),
  }));

  // Add radio groups as descriptors
  Object.entries(radioGroups).forEach(([name, grp], i) => {
    fieldDescriptors.push({
      index: fields.length + i,
      label: grp.label,
      type: "radio",
      name,
      options: grp.inputs.map(r => r.value),
    });
  });

  sendProgress({ status: "thinking", message: `AI (${activeLlm}) generating answers...` });

  let answers = {};
  try {
    const res = await fetch(`${apiUrl}/autofill`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ fields: fieldDescriptors, jd_text: jdText, company, llm: activeLlm }),
    });
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    answers = await res.json();
  } catch (err) {
    sendProgress({ status: "warning", message: `Backend unreachable. Using profile fallback.` });
    answers = await getLocalAnswers(fieldDescriptors);
  }

  let filled = 0, skipped = 0;

  // Fill regular fields
  for (let i = 0; i < fields.length; i++) {
    const field = fields[i];
    const answer = answers[field.label] || answers[field.name] || answers[String(i)];
    if (answer && answer !== "SKIP") {
      await delay(80);
      const ok = fillField(platform, field, answer);
      if (ok) {
        filled++;
        sendProgress({ status: "filling", message: `✓ ${field.label}`, filled, total: totalFields });
      } else skipped++;
    } else skipped++;
  }

  // Fill radio groups
  for (const [name, grp] of Object.entries(radioGroups)) {
    const answer = answers[grp.label] || answers[name];
    if (answer && answer !== "SKIP" && grp.container) {
      await delay(80);
      const ok = setRadioGroup(grp.container, answer);
      if (ok) {
        filled++;
        sendProgress({ status: "filling", message: `✓ ${grp.label} (radio)`, filled, total: totalFields });
      } else skipped++;
    } else skipped++;
  }

  sendProgress({
    status: "done",
    message: `Done! Filled ${filled} of ${totalFields} fields on ${platformName}`,
    filled, skipped, total: totalFields,
  });
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
  try { chrome.runtime.sendMessage({ action: "autofill_progress", data }); }
  catch (e) { /* popup may be closed */ }
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
    const llm = message.llm || null;
    runAutoFill(llm).catch(err => sendProgress({ status: "error", message: err.message }));
    sendResponse({ started: true });
    return true;
  }
  return true;
});
