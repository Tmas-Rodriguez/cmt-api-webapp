import subprocess
import json
import time
import requests
from pathlib import Path

# Folder to save the ngrok URL
output_folder = Path(r"P:\CurrentData")
output_folder.mkdir(parents=True, exist_ok=True)  # create folder if it doesn't exist
output_file = output_folder / "ngrok_url.txt"

def start_ngrok(port: int = 5000, outfile: Path = output_file):
    """
    Start ngrok tunnel, return the public URL, and save it to a file.
    Requires `ngrok` installed and authtoken configured.
    """
    # Start ngrok process in background
    ngrok = subprocess.Popen(
        ["ngrok", "http", str(port), "--log=stdout", "--log-format=json"],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True
    )

    # Wait a moment for ngrok to initialize
    time.sleep(2)

    # Query ngrok’s local API for tunnel info
    try:
        resp = requests.get("http://127.0.0.1:4040/api/tunnels")
        tunnels = resp.json()["tunnels"]
        public_url = tunnels[0]["public_url"]

        # Print
        print(f"✅ ngrok tunnel started: {public_url} -> http://localhost:{port}")
        print(f"🔗 URL saved to {outfile}")

        # Save to txt file
        outfile.write_text(public_url)

        return public_url, ngrok
    except Exception as e:
        print("❌ Failed to get ngrok tunnel:", e)
        ngrok.terminate()
        return None, None


if __name__ == "__main__":
    url, process = start_ngrok(5000)
    if url:
        print("Share this URL with your team:", url)
        print("Press CTRL+C to stop.")
        try:
            process.wait()  # keep tunnel alive
        except KeyboardInterrupt:
            print("\n🛑 Shutting down ngrok...")
            process.terminate()
