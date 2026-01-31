from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

app = FastAPI()

# SECURITY CONFIGURATION
# This tells the server: "Allow requests from Chrome Extensions"
origins = [
    "chrome-extension://*", 
    "http://localhost",
    "*"  # For development, we allow all. In production, we can tighten this.
]

app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.get("/")
def read_root():
    return {"status": "Server is running!"}

@app.get("/health")
def health_check():
    return {"message": "Connection Successful! Backend is ready."}