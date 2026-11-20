"""AeroPulse launcher — serves on http://127.0.0.1:8004"""
import uvicorn

HOST = "127.0.0.1"
PORT = 8004

if __name__ == "__main__":
    print(f"AeroPulse running at http://{HOST}:{PORT}")
    uvicorn.run("app.main:app", host=HOST, port=PORT)
