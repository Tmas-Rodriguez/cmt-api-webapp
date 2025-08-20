import subprocess
import os

def run_gera():
    env = os.environ.copy()
    env["PYTHONIOENCODING"] = "utf-8"
    process = subprocess.Popen(
        ["python", "-u", "gera.py"],  # <- add -u for unbuffered
        cwd="C:\\Users\\cmt\\Downloads\\cmt-api-copy\\cmt-api\\EsgCmt-API",
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
        env=env,
        encoding="utf-8"
    )
    for line in process.stdout:
        yield line.rstrip()
    process.stdout.close()
    process.wait()
