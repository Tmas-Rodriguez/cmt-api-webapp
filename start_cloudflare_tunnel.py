import subprocess
import time
import os

def main():

    tunnel_cmd = [
    "cloudflared", "tunnel", 
    "--protocol", "http2", 
    "--config", "C:\\Users\\cmt\\Documents\\Repsitories\\cmt-api-webapp\\config.yml", 
    "run", "5c506711-6cd2-44a8-ab84-250a66d37337"
]

    print("🌐 Iniciando Cloudflare Tunnel con configuración de ingress...")
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