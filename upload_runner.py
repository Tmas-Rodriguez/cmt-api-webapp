import json
import subprocess
import os
import time
from pathlib import Path
from urllib.parse import quote

import dentry_client
from planilla_v16 import latest_folder

PROVIDERS_DIR = Path(r"C:\Users\cmt\Documents\Repsitories\EsgCmt-API\CurrentData\providers")
SUCCESS_LINE = "¡Proceso completado exitosamente!"
APOLLO_TIMEOUT_SECONDS = 30 * 60


def run_upload(client_token=None, apollo_code=None):
    """upload_db.py's output. With `apollo_code` (clients whose planilla still lives outside
    Apollo), a successful upload also reloads that planilla in Apollo from the same folder; the
    success line is held back until then, so the panel finishes when Apollo is up to date too."""
    # the folder upload_db.py is about to upload (it picks the newest one the same way)
    folder = latest_folder(PROVIDERS_DIR / client_token) if client_token and apollo_code else None

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
    held = None
    for line in process.stdout:
        line = line.rstrip()
        if apollo_code and SUCCESS_LINE in line:
            held = line
            continue
        yield line
    process.stdout.close()
    process.wait()
    if held:
        if folder is not None:
            yield from reload_apollo(apollo_code, folder.name)
        yield held


def reload_apollo(code, folder_name):
    """Ask Apollo to reload the client's planilla from `folder_name` and stream its log. Never
    fails the upload (CMT already has the data): problems are reported as warnings."""
    yield f"🏛️ Apollo: actualizando la planilla con {folder_name}..."
    try:
        job = json.loads(dentry_client.request(
            f"/internal/cmt/reload_planilla/{code}?folder={quote(folder_name)}",
            scope="reload", method="POST"))["job"]
        since, deadline = 0, time.time() + APOLLO_TIMEOUT_SECONDS
        while time.time() < deadline:
            status = json.loads(dentry_client.request(
                f"/internal/cmt/reload_planilla/job/{job}?since={since}", scope="reload"))
            for line in status["lines"]:
                yield "   " + line
            since = status["next"]
            if status["done"]:
                if status["ok"]:
                    yield "✅ Apollo: planilla actualizada"
                else:
                    yield "⚠️ Apollo no se actualizó (la subida a CMT sí se hizo): ver el detalle arriba"
                return
            time.sleep(2)
        yield "⚠️ Apollo sigue recargando después de 30 minutos: revisalo en Apollo"
    except (RuntimeError, ValueError, KeyError) as error:
        yield f"⚠️ Apollo no se actualizó (la subida a CMT sí se hizo): {error}"
