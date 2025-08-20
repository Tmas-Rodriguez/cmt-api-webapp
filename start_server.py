import yaml
import subprocess
import os

def main():
    with open("tunnel.yml", "r") as f:
        config = yaml.safe_load(f)["tunnel"]

    port = config["port"]
    subdomain = config["subdomain"]

    print(f"🚀 Starting LocalTunnel on port {port} with subdomain '{subdomain}'...")

    # Full path to lt.cmd (installed by npm -g)
    lt_path = r"C:\Users\cmt\AppData\Roaming\npm\lt.cmd"

    try:
        subprocess.run(
            [lt_path, "--port", str(port), "--subdomain", subdomain],
            check=True
        )
    except KeyboardInterrupt:
        print("\n❌ Tunnel stopped by user.")

if __name__ == "__main__":
    main()