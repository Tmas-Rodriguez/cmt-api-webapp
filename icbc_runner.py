"""Alta de cuentas nuevas de ICBC directamente en producción.

A diferencia del resto de los clientes (que reconstruyen la base local y la suben
entera con import_db), acá se corre una receta de EsgCmt-API que INSERTA las cuentas
nuevas en la base productiva sin borrar nada:

    just account_<servicio>_prod cmt_icbc_prod <ruta al csv>

Las recetas viven en la rama ICBC_BRANCH, que no es la que tiene el árbol de trabajo
principal de EsgCmt-API. Para no mover esa rama ni tocar los cambios locales (p. ej. el
justfile modificado), la receta se corre desde un git worktree aparte con el justfile
tal cual viene en la rama. La carga es todo o nada: si hay errores no se escribe nada.
"""

import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

ESG_REPOSITORY = Path(r"C:\Users\cmt\Documents\Repsitories\EsgCmt-API")
ICBC_WORKTREE = ESG_REPOSITORY.parent / "EsgCmt-API-icbc"
ICBC_BRANCH = "feat/account-insert-prod-recipes"
ICBC_REMOTE_REF = f"origin/{ICBC_BRANCH}"
ICBC_DB = "cmt_icbc_prod"

# servicio (valor del selector) -> receta del justfile de la rama
ICBC_SERVICES = {
    "electricidad": ("Electricidad", "account_electricity_prod"),
    "gas": ("Gas", "account_gas_prod"),
    "agua": ("Agua", "account_water_prod"),
    "residuos": ("Residuos", "account_residue_prod"),
    "combustible": ("Combustible", "account_fuel_prod"),
    "otros": ("Otros", "account_other_prod"),
}

ERROR_MARKERS = ("PARSING ERRORS", "WRITING ERRORS")
ERRORS_MARKER = "__ICBC_ERRORS__:"
RESULT_MARKER = "__ICBC_RESULT__:"


def validate_csv(path):
    """(ok, mensaje). Chequeo mínimo: legible, no vacío y con la columna id_sitio."""
    try:
        with open(path, "r", encoding="utf-8-sig", errors="replace") as handle:
            lines = [line for line in handle.read().splitlines() if line.strip()]
    except OSError as error:
        return False, f"No se pudo leer el CSV: {error}"
    if len(lines) < 2:
        return False, "El CSV no tiene filas de datos (solo encabezado o vacío)."
    header = lines[0].replace(";", ",").split(",")
    if "id_sitio" not in [col.strip().strip('"').lower() for col in header]:
        return False, "El CSV no tiene la columna id_sitio en el encabezado."
    return True, f"CSV ok: {len(lines) - 1} fila(s) de datos."


def _git(*args, cwd=ESG_REPOSITORY):
    return subprocess.run(
        ["git", *args], cwd=str(cwd), capture_output=True, text=True,
        encoding="utf-8", errors="replace",
        env={**os.environ, "GIT_TERMINAL_PROMPT": "0"},
    )


def prepare_worktree():
    """Deja ICBC_WORKTREE en el commit de ICBC_REMOTE_REF, con node_modules y .env.script.

    Devuelve (ok, mensaje). No hace fetch ni toca el árbol de trabajo principal.
    """
    if _git("rev-parse", "--verify", "--quiet", ICBC_REMOTE_REF).returncode != 0:
        return False, (
            f"La rama {ICBC_BRANCH} no está en el repo local de EsgCmt-API "
            f"(falta {ICBC_REMOTE_REF}). Hacé un git fetch o verificá que exista en el remoto."
        )

    if not ICBC_WORKTREE.exists():
        result = _git("worktree", "add", "--detach", str(ICBC_WORKTREE), ICBC_REMOTE_REF)
        if result.returncode != 0:
            return False, f"No se pudo crear el worktree de ICBC: {result.stderr.strip()}"
    else:
        dirty = _git("status", "--porcelain", "--untracked-files=no", cwd=ICBC_WORKTREE)
        if dirty.returncode != 0:
            return False, f"El worktree de ICBC no es válido: {dirty.stderr.strip()}"
        if dirty.stdout.strip():
            return False, "El worktree de ICBC tiene cambios sin commitear; revisalo antes de seguir."
        result = _git("checkout", "--detach", ICBC_REMOTE_REF, cwd=ICBC_WORKTREE)
        if result.returncode != 0:
            return False, f"No se pudo actualizar el worktree de ICBC: {result.stderr.strip()}"

    # node_modules compartido (junction) y .env.script actual (CLOUD_DATABASE_URL).
    link = ICBC_WORKTREE / "node_modules"
    if not link.exists():
        made = subprocess.run(
            ["cmd", "/c", "mklink", "/J", str(link), str(ESG_REPOSITORY / "node_modules")],
            capture_output=True, text=True,
        )
        if made.returncode != 0:
            return False, f"No se pudo enlazar node_modules: {made.stdout.strip() or made.stderr.strip()}"
    env_script = ESG_REPOSITORY / ".env.script"
    if not env_script.is_file():
        return False, "No existe .env.script en EsgCmt-API (hace falta CLOUD_DATABASE_URL)."
    shutil.copyfile(env_script, ICBC_WORKTREE / ".env.script")
    return True, f"Worktree listo en {ICBC_REMOTE_REF}."


def _recipe_exists(recipe):
    result = subprocess.run(
        ["just", "--summary"], cwd=str(ICBC_WORKTREE), capture_output=True, text=True,
        encoding="utf-8", errors="replace",
    )
    return result.returncode == 0 and recipe in result.stdout.split()


def _collect_errors(since):
    files = []
    errors_dir = ICBC_WORKTREE / "errors"
    if errors_dir.is_dir():
        for path in sorted(errors_dir.glob("*.json")):
            if path.stat().st_mtime < since:
                continue
            try:
                content = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                content = path.read_text(encoding="utf-8", errors="replace")[:20000]
            files.append({"name": path.name, "content": content})
    return files


def run_icbc_accounts(service, csv_path):
    """Generador de líneas de log. Termina con RESULT_MARKER ok|error."""
    if service not in ICBC_SERVICES:
        yield "Servicio inválido."
        yield RESULT_MARKER + "error"
        return
    label, recipe = ICBC_SERVICES[service]

    yield f"Alta de cuentas ICBC · {label} · base {ICBC_DB} (PRODUCCIÓN)"

    ok, message = validate_csv(csv_path)
    yield message
    if not ok:
        yield RESULT_MARKER + "error"
        return

    yield "Preparando el entorno de la rama de ICBC..."
    ok, message = prepare_worktree()
    yield message
    if not ok:
        yield RESULT_MARKER + "error"
        return

    if not _recipe_exists(recipe):
        yield f"La receta {recipe} no existe en {ICBC_REMOTE_REF}. No se ejecutó nada."
        yield RESULT_MARKER + "error"
        return

    env = os.environ.copy()
    env["PYTHONIOENCODING"] = "utf-8"
    started = time.time()
    yield f"Ejecutando: just {recipe} {ICBC_DB} <csv>"
    process = subprocess.Popen(
        ["just", recipe, ICBC_DB, str(csv_path)],
        cwd=str(ICBC_WORKTREE),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        bufsize=1,
        universal_newlines=True,
        encoding="utf-8",
        errors="replace",
        env=env,
    )
    saw_error = False
    for line in iter(process.stdout.readline, ""):
        line = line.rstrip()
        if any(marker in line for marker in ERROR_MARKERS):
            saw_error = True
        yield line
    process.stdout.close()
    returncode = process.wait()

    error_files = _collect_errors(started - 2)
    if error_files:
        yield ERRORS_MARKER + json.dumps(error_files, ensure_ascii=False)

    if returncode != 0 or saw_error or error_files:
        yield "No se cargó ninguna cuenta: la carga es todo o nada. Corregí el CSV y volvé a correr."
        yield RESULT_MARKER + "error"
    else:
        yield "Proceso completado: sin errores, las cuentas quedaron cargadas. Verificalas en la webapp."
        yield RESULT_MARKER + "ok"
