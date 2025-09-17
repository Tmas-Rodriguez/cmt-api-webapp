import subprocess
import time
import sys
import os

def run_process(command, cwd=None):
    """
    Ejecuta un proceso en segundo plano y lo devuelve.
    """
    return subprocess.Popen(command, cwd=cwd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)

def main():
    # Ruta a tu app Flask
    flask_cmd = [sys.executable, "-m", "flask", "run", "--host=0.0.0.0", "--port=5000"]

    # Nombre del túnel (el mismo que creaste con cloudflared tunnel create)
    tunnel_name = "miapp"
    tunnel_cmd = ["cloudflared", "tunnel", "run", tunnel_name]

    print("🚀 Iniciando servidor Flask...")
    flask_proc = run_process(flask_cmd)

    time.sleep(5)  # espera un poco antes de iniciar el túnel

    print("🌐 Iniciando Cloudflare Tunnel...")
    tunnel_proc = run_process(tunnel_cmd)

    try:
        while True:
            # Monitorear si alguno de los procesos se cae
            if flask_proc.poll() is not None:
                print("⚠️ Flask se detuvo, reiniciando...")
                flask_proc = run_process(flask_cmd)

            if tunnel_proc.poll() is not None:
                print("⚠️ Cloudflare Tunnel se detuvo, reiniciando...")
                tunnel_proc = run_process(tunnel_cmd)

            time.sleep(10)

    except KeyboardInterrupt:
        print("🛑 Deteniendo procesos...")
        flask_proc.terminate()
        tunnel_proc.terminate()

if __name__ == "__main__":
    main()
