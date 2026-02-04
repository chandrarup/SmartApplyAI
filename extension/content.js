// content.js
chrome.runtime.onMessage.addListener((request, sender, sendResponse) => {
    if (request.action === "get_text") {
        // Simple scraper: Get all visible text
        const pageText = document.body.innerText;
        sendResponse({ text: pageText });
    }
});