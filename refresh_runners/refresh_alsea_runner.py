import subprocess
import os
import sys

companies = [
    {"id": "box1", "name": "Santander"},
    {"id": "box2", "name": "Grupo Petersen"},
    {"id": "box3", "name": "Alsea"},
    {"id": "box4", "name": "La Anónima"},
    {"id": "box5", "name": "ICBC"},
]

def run_refresh_alsea(client):

    company_name = next((c["name"] for c in companies if c["id"] == client), None)

    client = client[-1]

    yield f"Comenzando Refresh para cliente: {company_name}..."

    env = os.environ.copy()
    env["PYTHONIOENCODING"] = "utf-8"

    process = subprocess.Popen(
        [sys.executable, "-u", f"refreshDB_{company_name.lower().replace(" ", "_")}.py"],
        cwd="C:\\Users\\cmt\\Downloads\\cmt-api-copy\\cmt-api\\EsgCmt-API",
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        bufsize=1,
        universal_newlines=True,
        env=env,
    )

    # yield each line as it arrives
    for line in iter(process.stdout.readline, ""):
        yield line.rstrip()   # send to Flask
    process.stdout.close