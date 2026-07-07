// LocalHire Agent — Background Service Worker v2.1

const tabState = {};

chrome.runtime.onMessage.addListener((message, sender, sendResponse) => {
  const tabId = sender.tab?.id || message.tabId;

  if (message.action === "save_state") {
    tabState[tabId] = { ...tabState[tabId], ...message.data };
    sendResponse({ success: true });

  } else if (message.action === "get_state") {
    sendResponse(tabState[tabId] || {});

  } else if (message.action === "clear_state") {
    delete tabState[tabId];
    sendResponse({ success: true });

  } else if (message.action === "get_settings") {
    chrome.storage.sync.get(["apiUrl", "llm", "claudeKey", "autoRetrigger"], (result) => {
      sendResponse({
        url: result.apiUrl || "http://127.0.0.1:5001",
        llm: result.llm || "ollama",
        claudeKey: result.claudeKey || "",
        autoRetrigger: !!result.autoRetrigger,
      });
    });
    return true;

  } else if (message.action === "save_settings") {
    const payload = {
      apiUrl: message.url || "http://127.0.0.1:5001",
      llm: message.llm || "ollama",
      claudeKey: message.claudeKey || ""
    };
    if (typeof message.autoRetrigger === "boolean") payload.autoRetrigger = message.autoRetrigger;
    chrome.storage.sync.set(payload, () => sendResponse({ success: true }));
    return true;

  // Legacy compat — keep old get_api_url / save_api_url working
  } else if (message.action === "get_api_url") {
    chrome.storage.sync.get(["apiUrl"], (result) => {
      sendResponse({ url: result.apiUrl || "http://127.0.0.1:5001" });
    });
    return true;

  } else if (message.action === "save_api_url") {
    chrome.storage.sync.set({ apiUrl: message.url }, () => {
      sendResponse({ success: true });
    });
    return true;

  // Cache JD text from content script so popup can read it even after navigation
  } else if (message.action === "cache_page_context") {
    if (tabId) {
      tabState[tabId] = {
        ...tabState[tabId],
        pageContext: message.text,
        jobContext: message.jobContext || null,
        platform: message.platform,
        ts: Date.now()
      };
    }
    sendResponse({ success: true });

  } else if (message.action === "get_page_context") {
    const tid = message.tabId || tabId;
    const cached = tabState[tid];
    // Return cache if under 2 hours old
    if (cached?.pageContext && (Date.now() - (cached.ts || 0)) < 7200000) {
      sendResponse({ text: cached.pageContext, jobContext: cached.jobContext || null, platform: cached.platform, ts: cached.ts });
    } else {
      sendResponse({ text: null, jobContext: null });
    }
    return true;

  } else if (message.action === "fetch_json") {
    const url = message.url;
    if (!url || typeof url !== "string") {
      sendResponse({ ok: false, error: "missing url" });
      return true;
    }
    fetch(url, { method: message.method || "GET", headers: message.headers || {} })
      .then(async (r) => {
        const text = await r.text();
        sendResponse({ ok: r.ok, status: r.status, text });
      })
      .catch((e) => sendResponse({ ok: false, error: e.message || String(e) }));
    return true;
  }

  return true;
});

// Clean up state when tab closes
chrome.tabs.onRemoved.addListener((tabId) => {
  delete tabState[tabId];
});
