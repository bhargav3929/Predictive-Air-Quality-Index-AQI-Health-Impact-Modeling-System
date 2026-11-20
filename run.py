"""AeroPulse launcher. HOST and PORT come from .env (default 127.0.0.1:8004)."""
import uvicorn

from app import config

if __name__ == "__main__":
    print(f"AeroPulse running at http://{config.HOST}:{config.PORT}")
    uvicorn.run("app.main:app", host=config.HOST, port=config.PORT)
