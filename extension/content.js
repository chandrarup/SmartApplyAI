// content.js - Smart Scraper

function getCleanText() {
    // 1. Clone the body so we don't mess up the user's page
    const clone = document.body.cloneNode(true);

    // 2. Remove "Junk" elements that are definitely NOT the Job Description
    const junkSelectors = [
        'script', 'style', 'noscript', 'iframe', 'svg', 
        'nav', 'header', 'footer', 
        '[role="navigation"]', '[role="banner"]', '[role="contentinfo"]',
        '.nav', '.navigation', '.footer', '.header', '.ad', '.ads', '.cookie-banner'
    ];

    junkSelectors.forEach((selector) => {
        const elements = clone.querySelectorAll(selector);
        elements.forEach(el => el.remove());
    });

    // 3. Strategy: Find the "Main Job Container"
    // JDs usually live in <main>, <article>, or a div with "job" in the class name
    let mainContent = clone.querySelector('main') || clone.querySelector('article');

    // If no semantic tag, try to find a specific job container
    if (!mainContent) {
        // Look for divs with "job" or "description" in their class/id (common in Workday/Greenhouse)
        const possibleContainers = clone.querySelectorAll('div[class*="job"], div[class*="description"], div[id*="job"]');
        
        // Pick the one with the most text
        let maxTextLen = 0;
        possibleContainers.forEach(div => {
            if (div.innerText.length > maxTextLen) {
                maxTextLen = div.innerText.length;
                mainContent = div;
            }
        });
    }

    // Fallback: If smart detection fails, use the cleaned body
    const finalNode = mainContent || clone;
    
    // 4. Clean up whitespace (remove double spaces, empty lines)
    return finalNode.innerText.replace(/\s+/g, ' ').trim();
}

chrome.runtime.onMessage.addListener((request, sender, sendResponse) => {
    if (request.action === "get_text") {
        const cleanText = getCleanText();
        
        // LOGGING: This lets you inspect what the bot actually sees
        console.log("🤖 LocalHire Scraped Text:", cleanText);
        
        sendResponse({ text: cleanText });
    }
});