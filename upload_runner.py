import subprocess
import os

def run_upload(client_token=None):
    command = ["python", "-u", "upload_db.py"]
    if client_token:
        command.append(client_token)

    env = os.environ.copy()
    env["PYTHONIOENCODING"] = "utf-8"
    process = subprocess.Popen(
        command,  # <- add -u for unbuffered
        cwd="C:\\Users\\cmt\\Documents\\Repsitories\\EsgCmt-API",
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
