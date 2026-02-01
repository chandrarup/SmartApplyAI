document.getElementById('analyzeBtn').addEventListener('click', async () => {
    const resultDiv = document.getElementById('result');
    const loadingDiv = document.getElementById('loading');

    // Reset UI
    resultDiv.classList.add('hidden');
    loadingDiv.classList.remove('hidden');

    try {
        // 1. Find the active tab
        const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });

        // 2. Ask content.js to scrape the text
        chrome.tabs.sendMessage(tab.id, { action: "get_text" }, async (response) => {

            // Error handling if script didn't load
            if (chrome.runtime.lastError || !response || !response.text) {
                loadingDiv.innerText = "Error: Refresh the page and try again.";
                return;
            }

            // 3. Send scraped text to Python Backend
            try {
                const apiResponse = await fetch('http://127.0.0.1:8000/analyze', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ jd_text: response.text })
                });

                const data = await apiResponse.json();

                // 4. Show the AI Results
                loadingDiv.classList.add('hidden');
                resultDiv.classList.remove('hidden');

                resultDiv.innerHTML = `
                    <strong>Role:</strong> ${data.role}<br>
                    <strong>Score:</strong> ${data.score}<br>
                    <strong>Top Skills:</strong> ${data.skills.join(", ")}
                `;
            } catch (err) {
                loadingDiv.innerText = "Error: Is the Python backend running?";
                console.error(err);
            }
        });

    } catch (error) {
        loadingDiv.innerText = "Error: " + error.message;
    }
});