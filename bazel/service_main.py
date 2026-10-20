"""Start the standalone retrieval service with environment configuration."""
import os
import uvicorn

if __name__ == "__main__":
    uvicorn.run("api.main:app", host=os.environ.get("API_HOST", "127.0.0.1"),
                port=int(os.environ.get("API_PORT", "8000")))
