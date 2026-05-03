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
    chrome.storage.sync.get(["apiUrl", "llm", "claudeKey"], (result) => {
      sendResponse({
        url: result.apiUrl || "http://127.0.0.1:8000",
        llm: result.llm || "ollama",
        claudeKey: result.claudeKey || ""
      });
    });
    return true;

  } else if (message.action === "save_settings") {
    chrome.storage.sync.set({
      apiUrl: message.url || "http://127.0.0.1:8000",
      llm: message.llm || "ollama",
      claudeKey: message.claudeKey || ""
    }, () => sendResponse({ success: true }));
    return true;

  // Legacy compat — keep old get_api_url / save_api_url working
  } else if (message.action === "get_api_url") {
    chrome.storage.sync.get(["apiUrl"], (result) => {
      sendResponse({ url: result.apiUrl || "http://127.0.0.1:8000" });
    });
    return true;

  } else if (message.action === "save_api_url") {
    chrome.storage.sync.set({ apiUrl: message.url }, () => {
      sendResponse({ success: true });
    });
    return true;
  }

  return true;
});

// Clean up state when tab closes
chrome.tabs.onRemoved.addListener((tabId) => {
  delete tabState[tabId];
});
