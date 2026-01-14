import subprocess
import threading
import time
import os
from waitress import serve
from app import app  # your Flask app

LOG_FILE = "service.log"

def log(msg):
    with open(LOG_FILE, "a") as f:
        f.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')} - {msg}\n")

def run_tunnel():
    tunnel_name = "miapp"
    while True:
        try:
            log("Starting Cloudflare tunnel...")
            proc = subprocess.Popen(
                ["cloudflared", "tunnel", "run", tunnel_name],
                stdout=open(LOG_FILE, "a"),
                stderr=subprocess.STDOUT
            )
            proc.wait()
            log("Cloudflare tunnel exited, restarting in 5s...")
        except Exception as e:
            log(f"Tunnel error: {e}")
        time.sleep(5)

if __name__ == "__main__":
    log("Service starting...")

    # Start tunnel in background
    threading.Thread(target=run_tunnel, daemon=True).start()

    try:
        log("Starting Waitress server...")
        serve(app, host="127.0.0.1", port=5000)
    except Exception as e:
        log(f"Server error: {e}")

    log("Service main thread exiting (should never happen)")
