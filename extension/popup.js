// --- GLOBAL STATE (THE CACHE) ---
let globalPageContext = null; // Stores scraped text
let globalAnalysisData = null; // Stores match data for PDF
let chatHistory = []; // Stores chat messages

// --- TAB SWITCHING LOGIC ---
document.querySelectorAll('.tab').forEach(tab => {
    tab.addEventListener('click', () => {
        // UI Toggle
        document.querySelectorAll('.tab').forEach(t => t.classList.remove('active'));
        document.querySelectorAll('.content').forEach(c => c.classList.remove('active'));
        tab.classList.add('active');
        document.getElementById(tab.dataset.target).classList.add('active');

        // If switching to Chat, ensure we have context
        if (tab.dataset.target === 'tab-chat' && !globalPageContext) {
            scrapePage((text) => loadFAQs(text));
        }
    });
});

// --- HELPER: SCRAPE ONCE ---
function scrapePage(callback) {
    if (globalPageContext) {
        console.log("Using cached context");
        callback(globalPageContext);
        return;
    }

    console.log("Scraping page for the first time...");
    chrome.tabs.query({ active: true, currentWindow: true }, (tabs) => {
        chrome.tabs.sendMessage(tabs[0].id, { action: "get_text" }, (response) => {
            if (chrome.runtime.lastError || !response) {
                alert("Could not read page. Try refreshing.");
                return;
            }
            globalPageContext = response.text; // STORE IN CACHE
            callback(globalPageContext);
        });
    });
}

// --- TAB 1: MATCH LOGIC ---
document.getElementById('analyzeBtn').addEventListener('click', () => {
    const loading = document.getElementById('loading-match');
    const resultDiv = document.getElementById('match-result');
    const pdfBtn = document.getElementById('pdfBtn');

    loading.classList.remove('hidden');
    resultDiv.classList.add('hidden');
    pdfBtn.classList.add('hidden');

    scrapePage(async (text) => {
        try {
            const res = await fetch('http://127.0.0.1:8000/analyze', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ jd_text: text })
            });
            const data = await res.json();
            globalAnalysisData = data;

            loading.classList.add('hidden');
            resultDiv.classList.remove('hidden');
            resultDiv.innerHTML = `
                <strong>Role:</strong> ${data.role}<br>
                <strong>Match:</strong> <span style="color:blue">${data.score}</span><br>
                <strong>✅ Matched:</strong> ${data.skills_matched.join(", ")}<br>
                <div style="color:red; margin-top:5px;"><strong>❌ Missing:</strong> ${data.missing_skill}</div>
            `;
            pdfBtn.classList.remove('hidden');
        } catch (e) {
            loading.innerText = "Error: " + e.message;
        }
    });
});

document.getElementById('pdfBtn').addEventListener('click', async () => {
    const btn = document.getElementById('pdfBtn');
    btn.innerText = "Generating...";
    try {
        const res = await fetch('http://127.0.0.1:8000/generate-pdf', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(globalAnalysisData)
        });
        if (res.ok) {
            const blob = await res.blob();
            const url = window.URL.createObjectURL(blob);
            const a = document.createElement('a'); a.href = url; a.download = "Resume.pdf";
            document.body.appendChild(a); a.click(); document.body.removeChild(a);
            btn.innerText = "Done! ✅";
        }
    } catch (e) { btn.innerText = "Error"; }
});

// --- TAB 2: CHAT LOGIC ---

// 1. Load FAQs
async function loadFAQs(text) {
    const faqDiv = document.getElementById('faq-list');
    try {
        const res = await fetch('http://127.0.0.1:8000/suggest-questions', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ jd_text: text })
        });
        const questions = await res.json();

        faqDiv.innerHTML = ""; // Clear loading
        questions.forEach(q => {
            const btn = document.createElement('button');
            btn.className = 'faq-btn';
            btn.innerText = "❓ " + q;
            btn.onclick = () => sendChat(q); // Click to ask
            faqDiv.appendChild(btn);
        });
    } catch (e) { faqDiv.innerText = "Could not load FAQs."; }
}

// 2. Send Message
async function sendChat(msg = null) {
    const input = document.getElementById('chatInput');
    const question = msg || input.value;
    if (!question) return;

    // Add User Bubble
    addBubble(question, 'user');
    input.value = "";

    // Auto-scroll
    const historyDiv = document.getElementById('chat-history');
    historyDiv.scrollTop = historyDiv.scrollHeight;

    // Send to Backend (Using Cached Context)
    try {
        const res = await fetch('http://127.0.0.1:8000/chat', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                context: globalPageContext, // REUSE CACHE
                question: question,
                history: chatHistory
            })
        });
        const data = await res.json();

        // Add Bot Bubble
        addBubble(data.answer, 'bot');

        // Update History
        chatHistory.push({ role: 'user', content: question });
        chatHistory.push({ role: 'assistant', content: data.answer });
        historyDiv.scrollTop = historyDiv.scrollHeight;

    } catch (e) {
        addBubble("Error connecting to AI.", 'bot');
    }
}

function addBubble(text, type) {
    const div = document.createElement('div');
    div.className = `msg ${type}`;
    div.innerText = text;
    document.getElementById('chat-history').appendChild(div);
}

document.getElementById('sendChatBtn').addEventListener('click', () => sendChat());

// --- UTILITY: REFRESH ---
document.getElementById('refreshBtn').addEventListener('click', () => {
    globalPageContext = null; // Clear cache
    alert("Context cleared. Will rescrape next time.");
});