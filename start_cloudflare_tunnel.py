import subprocess
import time

def main():
    tunnel_cmd = ["cloudflared", "tunnel", "run", "miapp"]

    print("🌐 Iniciando Cloudflare Tunnel...")
    tunnel_proc = subprocess.Popen(tunnel_cmd)

    try:
        while True:
            if tunnel_proc.poll() is not None:
                print("⚠️ Tunnel caído, reiniciando...")
                tunnel_proc = subprocess.Popen(tunnel_cmd)

            time.sleep(10)

    except KeyboardInterrupt:
        print("🛑 Deteniendo Tunnel...")
        tunnel_proc.terminate()

if __name__ == "__main__":
    main()
