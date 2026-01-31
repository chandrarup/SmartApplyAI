document.getElementById('testBtn').addEventListener('click', async () => {
    const statusDiv = document.getElementById('status');
    statusDiv.innerText = "Connecting...";
    statusDiv.style.color = "orange";

    try {
        // Attempt to talk to your Python Backend
        const response = await fetch('http://127.0.0.1:8000/health');
        const data = await response.json();

        // If successful:
        statusDiv.innerText = data.message;
        statusDiv.style.color = "green";
    } catch (error) {
        // If failed (Server not running?):
        console.error(error);
        statusDiv.innerText = "Error: Is the backend running?";
        statusDiv.style.color = "red";
    }
});