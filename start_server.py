import subprocess
import time
import sys
import os

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
os.chdir(BASE_DIR)

def run_process(command, cwd=None):
    return subprocess.Popen(command, cwd=cwd)

def main():
    flask_cmd = [
        sys.executable,
        "-m", "flask", "run",
        "--host=0.0.0.0",
        "--port=5000"
    ]

    print("🚀 Iniciando servidor Flask...")
    flask_proc = run_process(flask_cmd)

    try:
        while True:
            if flask_proc.poll() is not None:
                print("⚠️ Flask se detuvo, reiniciando...")
                flask_proc = run_process(flask_cmd)

            time.sleep(10)

    except KeyboardInterrupt:
        print("🛑 Deteniendo Flask...")
        flask_proc.terminate()

if __name__ == "__main__":
    main()
