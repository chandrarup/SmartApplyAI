let currentAnalysisData = null; // Store the AI result globally

document.getElementById('analyzeBtn').addEventListener('click', async () => {
    const btn = document.getElementById('analyzeBtn');
    const resultDiv = document.getElementById('result');
    const loadingDiv = document.getElementById('loading');
    const pdfBtn = document.getElementById('pdfBtn'); // Get the new button

    // UI Reset
    resultDiv.classList.add('hidden');
    pdfBtn.classList.add('hidden'); // Hide PDF button while thinking
    loadingDiv.classList.remove('hidden');
    loadingDiv.innerText = "Reading page...";

    // 1. Disable immediately to prevent double-clicks
    btn.disabled = true; 
    btn.innerText = "Queued / Processing...";
    try {
        const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });

        // Step 1: Get Text
        chrome.tabs.sendMessage(tab.id, { action: "get_text" }, async (response) => {

            if (chrome.runtime.lastError || !response || !response.text) {
                loadingDiv.innerText = "Error: Refresh the page.";
                return;
            }

            loadingDiv.innerText = "Sending to AI...";

            try {
                // Step 2: Send to Python
                const apiResponse = await fetch('http://127.0.0.1:8000/analyze', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ jd_text: response.text })
                });

                if (!apiResponse.ok) throw new Error("Server Error");

                const data = await apiResponse.json();
                currentAnalysisData = data; // SAVE DATA FOR PDF STEP

                // Step 3: Display Results
                loadingDiv.classList.add('hidden');
                resultDiv.classList.remove('hidden');

                resultDiv.innerHTML = `
                    <div style="margin-bottom:8px;"><strong>Role:</strong> ${data.role}</div>
                    <div style="margin-bottom:8px; font-size: 16px;"><strong>Match:</strong> <span style="color:blue">${data.score}</span></div>
                    <div style="margin-bottom:8px;"><strong>✅ Matched:</strong> ${data.skills_matched.join(", ")}</div>
                    <div style="color:red;"><strong>❌ Missing:</strong> ${data.missing_skill}</div>
                    <div style="margin-top:10px; font-style:italic; font-size:12px; color:#555;">
                       <strong>Summary Plan:</strong> ${data.tailored_summary}
                    </div>
                `;

                // SHOW THE PDF BUTTON NOW
                pdfBtn.classList.remove('hidden');

            } catch (err) {
                console.error(err);
                loadingDiv.innerText = "Error: Is backend running?";
            }
        });

    } catch (error) {
        loadingDiv.innerText = "Error: " + error.message;
    }
});

// --- NEW PDF BUTTON LOGIC ---
document.getElementById('pdfBtn').addEventListener('click', async () => {
    const btn = document.getElementById('pdfBtn');
    const originalText = btn.innerText;
    btn.innerText = "Generating PDF (Wait ~30s)...";
    btn.disabled = true; // Prevent double clicks

    try {
        const response = await fetch('http://127.0.0.1:8000/generate-pdf', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(currentAnalysisData)
        });

        if (response.ok) {
            // Trigger Download
            const blob = await response.blob();
            const url = window.URL.createObjectURL(blob);
            const a = document.createElement('a');
            a.href = url;
            a.download = "Tailored_Resume.pdf";
            document.body.appendChild(a);
            a.click();
            document.body.removeChild(a);

            btn.innerText = "Done! ✅";
            setTimeout(() => { btn.innerText = originalText; btn.disabled = false; }, 3000);
        } else {
            btn.innerText = "Error Generating PDF";
            btn.disabled = false;
        }
    } catch (e) {
        console.error(e);
        btn.innerText = "Error";
        btn.disabled = false;
    }
    finally {
        btn.disabled = false;
        btn.innerText = "Analyze Job Description";
    }
});