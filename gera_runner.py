import subprocess
import os
import re
import json
import time
from pathlib import Path

# Path to store output folder info
GERA_OUTPUT_FOLDER = "C:\\Users\\cmt\\Documents\\Repsitories\\EsgCmt-API\\errors"  # Update this to match your output folder
ESG_REPOSITORY = Path(r"C:\Users\cmt\Documents\Repsitories\EsgCmt-API")
METRICS_SCRIPT = ESG_REPOSITORY / "compute_validation_metrics.py"
PROVIDERS_DIR = ESG_REPOSITORY / "CurrentData" / "providers"

# gera.py imprime "   📊 Base de datos: cmt_<cliente>_<fecha>" al armar el .env.script;
# lo leemos del stream para saber contra qué DB local calcular las métricas de validación.
DATABASE_NAME_RE = re.compile(r"Base de datos:\s*(\S+)")


def _client_dir(client_token):
    return PROVIDERS_DIR / client_token


def _load_json(path):
    try:
        if path.is_file():
            return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        pass
    return None


def _compute_metrics(database_name):
    """Corre compute_validation_metrics.py contra la DB local recién poblada por gera.py."""
    result = subprocess.run(
        ["python", str(METRICS_SCRIPT), database_name],
        cwd=str(ESG_REPOSITORY),
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        return None, (result.stderr or result.stdout or "compute_validation_metrics.py falló").strip()
    try:
        return json.loads(result.stdout.strip().splitlines()[-1]), None
    except (json.JSONDecodeError, IndexError):
        return None, "No se pudo interpretar la salida de compute_validation_metrics.py"


def _diff_metrics(current, production):
    """{service: {metric: {actual, produccion, delta}}} — solo métricas presentes en `current`."""
    diff = {}
    for service, current_values in current.items():
        prod_values = (production or {}).get(service, {})
        diff[service] = {}
        for metric, value in current_values.items():
            prod_value = prod_values.get(metric, 0) or 0
            diff[service][metric] = {
                "actual": value,
                "produccion": prod_value,
                "delta": value - prod_value,
            }
    return diff


def run_gera(client_token=None):
    # Record the start time before running the script
    start_time = time.time()

    command = ["python", "-u", "gera.py"]
    if client_token:
        command.append(client_token)

    env = os.environ.copy()
    env["PYTHONIOENCODING"] = "utf-8"
    process = subprocess.Popen(
        command,
        cwd="C:\\Users\\cmt\\Documents\\Repsitories\\EsgCmt-API",
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
        env=env,
        encoding="utf-8"
    )
    database_name = None
    for line in process.stdout:
        match = DATABASE_NAME_RE.search(line)
        if match:
            database_name = match.group(1)
        yield line.rstrip()
    process.stdout.close()
    return_code = process.wait()

    # Signal completion with a special marker including the start time
    if return_code == 0:
        if database_name and client_token:
            current, error = _compute_metrics(database_name)
            if current is None:
                yield f"⚠️ No se pudieron calcular las métricas de validación: {error}"
            else:
                client_dir = _client_dir(client_token)
                client_dir.mkdir(parents=True, exist_ok=True)
                production = _load_json(client_dir / "production_metrics.json")
                diff = _diff_metrics(current, production)
                try:
                    (client_dir / "validation_metrics.json").write_text(
                        json.dumps(diff, ensure_ascii=False, indent=2), encoding="utf-8"
                    )
                except OSError as e:
                    yield f"⚠️ No se pudo guardar validation_metrics.json: {e}"
                yield f"__GERA_METRICS__:{json.dumps(diff, ensure_ascii=False)}"
        yield f"__GERA_COMPLETED__:{GERA_OUTPUT_FOLDER}|{start_time}"
    else:
        yield f"__GERA_ERROR__:{GERA_OUTPUT_FOLDER}|{start_time}|{return_code}"
