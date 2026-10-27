"""Alta de cuentas nuevas directamente en producción (añadido al flujo normal).

El flujo normal (Validate / Upload) sigue igual: arma la base local y la sube entera con
import_db, reemplazando las colecciones. Esto es un añadido para cuando solo hay que SUMAR
cuentas: se corre una receta de EsgCmt-API que INSERTA las cuentas nuevas en la base
productiva del cliente sin borrar nada:

    just account_<servicio>_prod cmt_<cliente>_prod <ruta al csv>

Las cuentas salen de la planilla del cliente en el data entry (cuentas_<x>, exportado en el
momento) menos las que ya están en producción. Las recetas viven en la rama RECIPES_BRANCH
(basada en patron-v1.5), que no es la que tiene el árbol de trabajo principal de
EsgCmt-API. Para no mover esa rama ni tocar los cambios locales (p. ej. el justfile
modificado), la receta se corre desde un git worktree aparte con el justfile tal cual viene
en la rama. La carga es todo o nada: si hay errores no se escribe nada.

Por ahora solo para ICBC (ACCOUNT_CLIENTS): el resto de los clientes se suma acá cuando haga
falta. Además tienen que ser de patron v1.5: el parser de cuentas de v1.6 es distinto
(ver supported()) y las recetas de esa rama no están pensadas para esa versión.
"""

import csv
import io
import json
import os
import shutil
import subprocess
import tempfile
import time
from pathlib import Path

ESG_REPOSITORY = Path(r"C:\Users\cmt\Documents\Repsitories\EsgCmt-API")
# Worktree de la rama de recetas (se creó para ICBC, de ahí el nombre de la carpeta).
RECIPES_WORKTREE = ESG_REPOSITORY.parent / "EsgCmt-API-icbc"
RECIPES_BRANCH = "feat/account-insert-prod-recipes"
RECIPES_REMOTE_REF = f"origin/{RECIPES_BRANCH}"
RECIPES_PATRON_BRANCH = "patron-v1.5"   # la rama de recetas parte de esta versión del patrón

# id de la caja en la webapp (o "icbc", que no tiene caja) ->
#   name  texto en la UI
#   code  código del cliente en el data entry (CLIENT_BY_CODE)
#   db    base de producción en Mongo
#   patron_branch  versión del patrón si no figura en client_branches.json
# Solo ICBC por ahora: Alsea pasó a patron v1.6 el 2026-10-27 (estas recetas son de v1.5) y los
# demás clientes (box1 Santander, box2 GP, box4 La Anonima, box5 Mostaza, box6 DIA) no aparecen
# en la lista y el servidor los rechaza.
ACCOUNT_CLIENTS = {
    "icbc": {"name": "ICBC", "code": "ICBC", "db": "cmt_icbc_prod", "patron_branch": "patron-v1.5"},
}

# servicio (valor del selector) ->
#   label      texto en la UI
#   recipe     receta del justfile de la rama
#   table      sufijo de cuentas_<x> en la planilla del data entry
#   key_col    columna del CSV que es el número de cuenta en Mongo (account.number)
#   collection colección de cuentas en Mongo
#   mongo_service valor de `service` en Mongo
ACCOUNT_SERVICES = {
    "electricidad": {"label": "Electricidad", "recipe": "account_electricity_prod", "table": "e",
                     "key_col": "cuenta", "collection": "account", "mongo_service": "electricidad"},
    "gas": {"label": "Gas", "recipe": "account_gas_prod", "table": "g",
            "key_col": "cuenta", "collection": "account", "mongo_service": "gas"},
    "agua": {"label": "Agua", "recipe": "account_water_prod", "table": "a",
             "key_col": "cuenta", "collection": "account", "mongo_service": "agua"},
    "residuos": {"label": "Residuos", "recipe": "account_residue_prod", "table": "r",
                 "key_col": "nombre_cuenta", "collection": "account_residue", "mongo_service": "residuo"},
    "combustible": {"label": "Combustible", "recipe": "account_fuel_prod", "table": "c",
                    "key_col": "nombre_cuenta", "collection": "account_fuel", "mongo_service": "combustible"},
    "otros": {"label": "Otros", "recipe": "account_other_prod", "table": "o",
              "key_col": "nombre_cuenta", "collection": "account_other", "mongo_service": "otro"},
}

# Colección de facturas de cada servicio en Mongo (para avisar si una cuenta tiene facturas).
INVOICE_COLLECTIONS = {
    "electricidad": "invoice", "gas": "invoice", "agua": "invoice",
    "residuo": "invoice_residue", "combustible": "invoice_fuel", "otro": "invoice_other",
}
MAX_INVOICE_COUNTS = 100   # solo se cuentan las facturas de las primeras N cuentas "solo en producción"

ENV_SCRIPT = ESG_REPOSITORY / ".env.script"

ERROR_MARKERS = ("PARSING ERRORS", "WRITING ERRORS")
ERRORS_MARKER = "__ACCOUNTS_ERRORS__:"
RESULT_MARKER = "__ACCOUNTS_RESULT__:"


def supported(client_id, client_branches=None):
    """(ok, motivo). Solo los clientes de patron v1.5 pueden usar las recetas de la rama.

    `client_branches` es el contenido de client_branches.json ({box_id: {"branch": ...}}):
    si el cliente figura ahí manda esa versión; si no, la de ACCOUNT_CLIENTS.
    """
    cfg = ACCOUNT_CLIENTS.get(client_id)
    if cfg is None:
        return False, "Cliente inválido."
    branch = ((client_branches or {}).get(client_id) or {}).get("branch") or cfg["patron_branch"]
    if branch != RECIPES_PATRON_BRANCH:
        return False, (
            f"{cfg['name']} usa {branch}: las recetas de {RECIPES_BRANCH} parten de "
            f"{RECIPES_PATRON_BRANCH} y el parser de cuentas de v1.6 es distinto. "
            "Hace falta una versión de las recetas basada en v1.6."
        )
    return True, ""


def _cloud_mongo_url():
    """CLOUD_DATABASE_URL activa en el .env.script de EsgCmt-API (solo lectura)."""
    if ENV_SCRIPT.is_file():
        for line in ENV_SCRIPT.read_text(encoding="utf-8").splitlines():
            if line.lstrip().startswith("#"):
                continue
            key, sep, value = line.partition("=")
            if sep and key.strip() == "CLOUD_DATABASE_URL":
                return value.strip().strip('"').strip("'")
    raise RuntimeError("No se encontró CLOUD_DATABASE_URL en el .env.script de EsgCmt-API.")


def fetch_cuentas_csv(client_id, service):
    """cuentas_<x>.csv de la planilla del cliente, exportado en el momento por el data entry."""
    import dentry_client

    code = ACCOUNT_CLIENTS[client_id]["code"]
    table = ACCOUNT_SERVICES[service]["table"]
    try:
        return dentry_client.request(f"/internal/cmt/cuentas/{code}/{table}.csv", scope="cuentas")
    except RuntimeError as error:
        if "404" in str(error):
            raise RuntimeError(
                f"El data entry no tiene la tabla cuentas_{table} para {ACCOUNT_CLIENTS[client_id]['name']} "
                "(o todavía no tiene la versión con las rutas internas)."
            ) from error
        raise


def existing_accounts(client_id, service):
    """{(siteCode, number, supplier)} de las cuentas del servicio ya cargadas en producción."""
    from pymongo import MongoClient

    cfg = ACCOUNT_SERVICES[service]
    client = MongoClient(_cloud_mongo_url(), serverSelectionTimeoutMS=10000)
    try:
        cursor = client[ACCOUNT_CLIENTS[client_id]["db"]][cfg["collection"]].find(
            {"service": cfg["mongo_service"]}, {"_id": 0, "siteCode": 1, "number": 1, "supplier": 1}
        )
        return {(str(d.get("siteCode")), str(d.get("number")), str(d.get("supplier"))) for d in cursor}
    except Exception as error:  # noqa: BLE001
        raise RuntimeError(f"No se pudo leer las cuentas de producción: {error}") from error
    finally:
        client.close()


def count_invoices(client_id, service, keys):
    """{(siteCode, number, supplier): cantidad de facturas} para las primeras MAX_INVOICE_COUNTS claves.

    Sirve para saber si una cuenta que sobra en producción tiene facturas colgando. Las demás
    claves quedan en None (no se contaron).
    """
    from pymongo import MongoClient

    cfg = ACCOUNT_SERVICES[service]
    counts = {key: None for key in keys}
    client = MongoClient(_cloud_mongo_url(), serverSelectionTimeoutMS=10000)
    try:
        collection = client[ACCOUNT_CLIENTS[client_id]["db"]][INVOICE_COLLECTIONS[cfg["mongo_service"]]]
        for site_code, number, supplier in keys[:MAX_INVOICE_COUNTS]:
            counts[(site_code, number, supplier)] = collection.count_documents({
                "service": cfg["mongo_service"], "siteCode": site_code,
                "accountNumber": number, "supplier": supplier,
            })
    except Exception as error:  # noqa: BLE001
        raise RuntimeError(f"No se pudieron contar las facturas de las cuentas de producción: {error}") from error
    finally:
        client.close()
    return counts


def split_new_accounts(service, csv_bytes, existing):
    """Separa del cuentas_<x> de la planilla las cuentas que todavía no están en producción.

    Devuelve {total, prod_total, existing, new, items, only_prod_keys, csv_bytes}: csv_bytes
    solo con las nuevas, mismo encabezado y mismo formato que el export (todo entrecomillado).
    La comparación es total, en los dos sentidos: además de las nuevas (en la planilla y no en
    producción) devuelve las claves "solo en producción" (en producción y no en la planilla).
    """
    key_col = ACCOUNT_SERVICES[service]["key_col"]
    reader = csv.DictReader(io.StringIO(csv_bytes.decode("utf-8-sig")))
    header = reader.fieldnames or []
    missing = [c for c in ("id_sitio", key_col, "proveedor") if c not in header]
    if missing:
        raise RuntimeError(f"El export de la planilla no trae las columnas: {', '.join(missing)}.")
    rows = [row for row in reader if any((v or "").strip() for v in row.values())]
    key_of = lambda row: (row["id_sitio"].strip(), row[key_col].strip(), row["proveedor"].strip())  # noqa: E731
    planilla_keys = {key_of(row) for row in rows}
    new_rows = [row for row in rows if key_of(row) not in existing]
    out = io.StringIO()
    writer = csv.DictWriter(out, fieldnames=header, quoting=csv.QUOTE_ALL, lineterminator="\r\n")
    writer.writeheader()
    writer.writerows(new_rows)
    return {
        "total": len(rows),
        "prod_total": len(existing),
        "only_prod_keys": sorted(existing - planilla_keys),
        "existing": len(rows) - len(new_rows),
        "new": len(new_rows),
        "items": [
            {"id_sitio": r["id_sitio"], "cuenta": r[key_col], "proveedor": r["proveedor"]}
            for r in new_rows
        ],
        "csv_bytes": out.getvalue().encode("utf-8"),
    }


def prepare_preview(client_id, service):
    """Exporta la planilla, descarta lo que ya está en producción y deja el CSV de las nuevas
    en una carpeta temporal. Devuelve el resumen + temp_dir/csv_path."""
    result = split_new_accounts(
        service, fetch_cuentas_csv(client_id, service), existing_accounts(client_id, service)
    )
    only_prod_keys = result.pop("only_prod_keys")
    invoices = count_invoices(client_id, service, only_prod_keys) if only_prod_keys else {}
    result["only_prod"] = [
        {"id_sitio": k[0], "cuenta": k[1], "proveedor": k[2], "facturas": invoices.get(k)}
        for k in only_prod_keys
    ]
    temp_dir = tempfile.mkdtemp(prefix="prod_accounts_")
    csv_path = os.path.join(temp_dir, "cuentas.csv")
    with open(csv_path, "wb") as handle:
        handle.write(result.pop("csv_bytes"))
    return {**result, "temp_dir": temp_dir, "csv_path": csv_path}


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
    """Deja RECIPES_WORKTREE en el commit de RECIPES_REMOTE_REF, con node_modules y .env.script.

    Devuelve (ok, mensaje). No hace fetch ni toca el árbol de trabajo principal.
    """
    if _git("rev-parse", "--verify", "--quiet", RECIPES_REMOTE_REF).returncode != 0:
        return False, (
            f"La rama {RECIPES_BRANCH} no está en el repo local de EsgCmt-API "
            f"(falta {RECIPES_REMOTE_REF}). Hacé un git fetch o verificá que exista en el remoto."
        )

    if not RECIPES_WORKTREE.exists():
        result = _git("worktree", "add", "--detach", str(RECIPES_WORKTREE), RECIPES_REMOTE_REF)
        if result.returncode != 0:
            return False, f"No se pudo crear el worktree de recetas: {result.stderr.strip()}"
    else:
        dirty = _git("status", "--porcelain", "--untracked-files=no", cwd=RECIPES_WORKTREE)
        if dirty.returncode != 0:
            return False, f"El worktree de recetas no es válido: {dirty.stderr.strip()}"
        if dirty.stdout.strip():
            return False, "El worktree de recetas tiene cambios sin commitear; revisalo antes de seguir."
        result = _git("checkout", "--detach", RECIPES_REMOTE_REF, cwd=RECIPES_WORKTREE)
        if result.returncode != 0:
            return False, f"No se pudo actualizar el worktree de recetas: {result.stderr.strip()}"

    # node_modules compartido (junction) y .env.script actual (CLOUD_DATABASE_URL).
    link = RECIPES_WORKTREE / "node_modules"
    if not link.exists():
        made = subprocess.run(
            ["cmd", "/c", "mklink", "/J", str(link), str(ESG_REPOSITORY / "node_modules")],
            capture_output=True, text=True,
        )
        if made.returncode != 0:
            return False, f"No se pudo enlazar node_modules: {made.stdout.strip() or made.stderr.strip()}"
    # Los entrypoints escriben sus reportes en ./errors/, que está en .gitignore y por eso
    # no existe en un worktree recién creado (sin la carpeta, writeFileSync fallaría).
    (RECIPES_WORKTREE / "errors").mkdir(exist_ok=True)
    env_script = ESG_REPOSITORY / ".env.script"
    if not env_script.is_file():
        return False, "No existe .env.script en EsgCmt-API (hace falta CLOUD_DATABASE_URL)."
    shutil.copyfile(env_script, RECIPES_WORKTREE / ".env.script")
    return True, f"Worktree listo en {RECIPES_REMOTE_REF}."


def _recipe_exists(recipe):
    result = subprocess.run(
        ["just", "--summary"], cwd=str(RECIPES_WORKTREE), capture_output=True, text=True,
        encoding="utf-8", errors="replace",
    )
    return result.returncode == 0 and recipe in result.stdout.split()


def _collect_errors(since):
    files = []
    errors_dir = RECIPES_WORKTREE / "errors"
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


def run_prod_accounts(client_id, service, csv_path, client_branches=None):
    """Generador de líneas de log. Termina con RESULT_MARKER ok|error."""
    cfg = ACCOUNT_CLIENTS.get(client_id)
    if cfg is None or service not in ACCOUNT_SERVICES:
        yield "Cliente o servicio inválido."
        yield RESULT_MARKER + "error"
        return
    ok, reason = supported(client_id, client_branches)
    if not ok:
        yield reason
        yield RESULT_MARKER + "error"
        return
    label, recipe, db = ACCOUNT_SERVICES[service]["label"], ACCOUNT_SERVICES[service]["recipe"], cfg["db"]

    yield f"Alta de cuentas {cfg['name']} · {label} · base {db} (PRODUCCIÓN)"

    ok, message = validate_csv(csv_path)
    yield message
    if not ok:
        yield RESULT_MARKER + "error"
        return

    yield "Preparando el entorno de la rama de recetas..."
    ok, message = prepare_worktree()
    yield message
    if not ok:
        yield RESULT_MARKER + "error"
        return

    if not _recipe_exists(recipe):
        yield f"La receta {recipe} no existe en {RECIPES_REMOTE_REF}. No se ejecutó nada."
        yield RESULT_MARKER + "error"
        return

    env = os.environ.copy()
    env["PYTHONIOENCODING"] = "utf-8"
    started = time.time()
    yield f"Ejecutando: just {recipe} {db} <csv>"
    process = subprocess.Popen(
        ["just", recipe, db, str(csv_path)],
        cwd=str(RECIPES_WORKTREE),
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
        yield "No se cargó ninguna cuenta: la carga es todo o nada. Corregí lo que indican los errores y volvé a correr."
        yield RESULT_MARKER + "error"
    else:
        yield "Proceso completado: sin errores, las cuentas quedaron cargadas. Verificalas en la webapp."
        yield RESULT_MARKER + "ok"
