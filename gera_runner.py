import subprocess
import os
import json
import time

# Path to store output folder info
GERA_OUTPUT_FOLDER = "C:\\Users\\cmt\\Documents\\Repsitories\\EsgCmt-API\\errors"  # Update this to match your output folder

def run_gera():
    # Record the start time before running the script
    start_time = time.time()
    
    env = os.environ.copy()
    env["PYTHONIOENCODING"] = "utf-8"
    process = subprocess.Popen(
        ["python", "-u", "gera.py"],  # <- add -u for unbuffered
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
    return_code = process.wait()
    
    # Signal completion with a special marker including the start time
    if return_code == 0:
        yield f"__GERA_COMPLETED__:{GERA_OUTPUT_FOLDER}|{start_time}"
    else:
        yield f"__GERA_ERROR__:{GERA_OUTPUT_FOLDER}|{start_time}|{return_code}"

