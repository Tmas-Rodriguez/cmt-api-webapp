import win32serviceutil
import win32service
import win32event
import servicemanager
import subprocess
import threading
import time
import os
from waitress import serve
from app import app  # Import your Flask app here

# Optional: log file path
LOG_FILE = os.path.join(os.path.dirname(__file__), "flask_service.log")

def log(msg):
    timestamp = time.strftime("%Y-%m-%d %H:%M:%S")
    with open(LOG_FILE, "a") as f:
        f.write(f"[{timestamp}] {msg}\n")

class FlaskService(win32serviceutil.ServiceFramework):
    _svc_name_ = "FlaskApp"
    _svc_display_name_ = "FlaskApp Service"
    _svc_description_ = "Flask + Waitress app with Cloudflare Tunnel"

    def __init__(self, args):
        win32serviceutil.ServiceFramework.__init__(self, args)
        self.stop_event = win32event.CreateEvent(None, 0, 0, None)
        self.should_stop = False

    def SvcStop(self):
        log("Service stopping...")
        self.ReportServiceStatus(win32service.SERVICE_STOP_PENDING)
        self.should_stop = True
        win32event.SetEvent(self.stop_event)

    def SvcDoRun(self):
        log("Service starting...")

        # Report running immediately
        self.ReportServiceStatus(win32service.SERVICE_RUNNING)
        
        # Start Cloudflared tunnel in a separate thread
        def tunnel_thread():
            tunnel_name = "miapp"
            while not self.should_stop:
                try:
                    log("Starting Cloudflared tunnel...")
                    proc = subprocess.Popen(
                        ["cloudflared", "tunnel", "run", tunnel_name],
                        stdout=open(LOG_FILE, "a"),
                        stderr=subprocess.STDOUT
                    )
                    while proc.poll() is None and not self.should_stop:
                        time.sleep(1)
                    if not self.should_stop:
                        log("Cloudflared exited unexpectedly, restarting in 5s...")
                        time.sleep(5)
                except Exception as e:
                    log(f"Cloudflared error: {e}")
                    time.sleep(5)

        threading.Thread(target=tunnel_thread, daemon=True).start()

        # Start Waitress server (blocks main thread)
        try:
            log("Starting Waitress server...")
            serve(app, host="127.0.0.1", port=5000)
        except Exception as e:
            log(f"Waitress server error: {e}")

        log("Service main thread exiting.")

if __name__ == "__main__":
    win32serviceutil.HandleCommandLine(FlaskService)
