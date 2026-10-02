import os
os.chdir(os.path.dirname(os.path.abspath(__file__)))
from flask import Flask, render_template, request, jsonify, Response, redirect, url_for, flash, send_file
from flask_login import LoginManager, UserMixin, login_user, login_required, logout_user, current_user
from werkzeug.utils import secure_filename
from flask import stream_with_context
import io

import subprocess
import shutil
import zipfile
import json
import re
import tempfile
import threading
import queue
import time
from datetime import datetime
from pathlib import Path
from uuid import uuid4
from gera_runner import run_gera
from refresh_runners.refresh_runner import run_refresh
from upload_runner import run_upload
from sheets_runner import import_sheet
from file_handler import save_file_to_organized_folder, save_local_file_to_organized_folder
import aws_sso
import locks

app = Flask(__name__)

# Configuration for file uploads
UPLOAD_FOLDER = r"C:\Users\cmt\Documents\Repsitories\EsgCmt-API\CurrentData\providers"
ESG_REPOSITORY = Path(r"C:\Users\cmt\Documents\Repsitories\EsgCmt-API")
SWITCH_PATRON_SCRIPT = ESG_REPOSITORY / "switch-patron.ps1"
BRANCH_HISTORY_FILE = Path(__file__).resolve().parent / "branch_history.json"
# Rama de patron asignada a cada cliente (varios clientes pueden compartir rama).
# COMPLETAR/REVISAR antes de confiar en el cambio automático al presionar "Validate".
CLIENT_BRANCHES_FILE = Path(__file__).resolve().parent / "client_branches.json"
# Carpeta donde el refresh (match_query, invoice_files_clean, pdf_finder) deja
# sus reportes JSON de error. Se descargan al finalizar el refresh.
ERRORS_DIR = ESG_REPOSITORY / "errors"
# Contador de PDFs sin matchear por cliente (lo escribe pdf_finder en cada refresh).
PDF_COUNTS_FILE = ESG_REPOSITORY / "pdf_unmatched_counts.json"
PDF_LIST_FILE = ESG_REPOSITORY / "pdf_unmatched_list.json"


def load_pdf_counts():
    """Devuelve {token: {count, updatedAt, ...}} desde PDF_COUNTS_FILE (o {})."""
    try:
        if PDF_COUNTS_FILE.is_file():
            data = json.loads(PDF_COUNTS_FILE.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                return data
    except (json.JSONDecodeError, OSError):
        pass
    return {}


def load_pdf_unmatched_list(token):
    """Devuelve {updatedAt, total, items} de las facturas sin PDF del cliente, o None."""
    try:
        if PDF_LIST_FILE.is_file():
            data = json.loads(PDF_LIST_FILE.read_text(encoding="utf-8"))
            entry = data.get((token or "").strip().lower()) if isinstance(data, dict) else None
            if isinstance(entry, dict):
                return entry
    except (json.JSONDecodeError, OSError):
        pass
    return None


def load_client_branches():
    """Devuelve {client_id: {"name", "branch"}} desde CLIENT_BRANCHES_FILE (o {})."""
    try:
        if CLIENT_BRANCHES_FILE.is_file():
            data = json.loads(CLIENT_BRANCHES_FILE.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                return data
    except (json.JSONDecodeError, OSError):
        pass
    return {}
ALLOWED_EXTENSIONS = {'xlsx', 'xls', 'csv'}

# Create upload folder if it doesn't exist
os.makedirs(UPLOAD_FOLDER, exist_ok=True)

app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER
app.config['MAX_CONTENT_LENGTH'] = 100 * 1024 * 1024  # 100MB max file size

def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS
gera_state = {"output_folder": None, "start_time": None}
# Marca de tiempo de inicio del último refresh, para bajar solo los archivos de
# error generados por esa corrida (mismo criterio que la descarga de gera).
refresh_state = {"start_time": None}

companies = [
    {"id": "box1", "name": "Santander", "token": "santander"},
    {"id": "box2", "name": "Grupo Petersen", "token": "gp"},
    {"id": "box3", "name": "Alsea", "token": "alsea"},
    {"id": "box4", "name": "La Anonima", "token": "laanonima"},
    {"id": "box5", "name": "Mostaza", "token": "mostaza"},
    {"id": "box6", "name": "DIA", "token": "dia"},
    {"id": "box7", "name": "Demo", "token": "demo"},
]


def resolve_company(selected_id):
    return next((c for c in companies if c["id"] == selected_id), None)


import_jobs = {}
import_jobs_lock = threading.Lock()

# "Importar desde Sheets": planilla de Google Sheets por cliente.
#   token    -> nombre de cliente para la carpeta csv_{token}_{fecha}. DEBE ser una
#              sola palabra ASCII y coincidir con una clave de CLIENT_BUCKETS en
#              EsgCmt-API/gera.py (gera valida con el regex csv_([a-zA-Z]+)_).
#   sheet_id -> ID del Google Sheet (el tramo entre /d/ y /edit de la URL).
#              La service account debe tener permiso de lectura sobre cada planilla.
# COMPLETAR los sheet_id vacíos antes de habilitar el botón para cada cliente.
CLIENT_SHEETS = {
    "box1": {"token": "san",     "sheet_id": "1CeXtrSc2NJvn6pRYD9NDMjLi4RolkiRhJbAEF4r8bX8"},  # Santander
    "box2": {"token": "gp",      "sheet_id": "13Op3wmHnI9zCVnLGs7f9uMr35jjWQmzYIhfx79Z8d6M"},  # Grupo Petersen
    "box3": {"token": "alsea",   "sheet_id": "105aDRdy-E9VItBgm529_AF6Muxc3vsi-mtB4IIDhXEQ"},  # Alsea
    "box4": {"token": "la",      "sheet_id": "1CX4WSq7PR-1a6FWstM-DdnqlKgo1_VNiohoQ8e8AiMI"},  # La Anonima
    "box5": {"token": "mostaza", "sheet_id": "1fpNxj9lToSOfBIrzALtyoIcTXwOvskUoj1h0yK92-0g"},  # Mostaza
    "box6": {"token": "dia",     "sheet_id": "1zLxmB7vndMRh2P7iJbitpPMkd94ZHtJ6Q-VtytJcAbM"},  # DIA
    "box7": {"token": "demo",    "sheet_id": "1bu0LYCkceLbgDEsf6yRyKc18rhfJeZCelexj7gm2AWw"},  # Demo
}

app.secret_key = "supersecret"

login_manager = LoginManager()
login_manager.init_app(app)
# login_manager.login_view = "login"

@login_manager.unauthorized_handler
def unauthorized():
    """Handle unauthorized requests - return JSON for AJAX, redirect for browser"""
    if request.is_json or request.path.startswith(('/stream-', '/download-', '/upload-', '/run-errors/', '/pdf-unmatched-counts', '/pdf-unmatched/', '/import-from-sheets/status/')):
        return jsonify({"status": "error", "message": "Unauthorized"}), 401
    return redirect(url_for("login"))

# Dummy user (replace with DB lookup)
class User(UserMixin):
    def __init__(self, id, name, password):
        self.id = id
        self.name = name
        self.password = password

users = {
    "tomi": User(id="tomi", name="Tomi", password="tomipass"),
    "marce": User(id="marce", name="Marce", password="marcepass"),
    "santi": User(id="santi", name="Santi", password="santipass"),
}

@login_manager.user_loader
def load_user(user_id):
    return users.get(user_id)

@app.route("/")
@login_required
def index():
    counts = load_pdf_counts()
    enriched = []
    for company in companies:
        entry = counts.get((company.get("token") or "").strip().lower())
        enriched.append({
            **company,
            "unmatched": entry.get("count") if isinstance(entry, dict) else None,
        })
    return render_template("index.html", companies=enriched)


@app.route("/pdf-unmatched-counts")
@login_required
def pdf_unmatched_counts():
    """Contadores de PDFs sin matchear por cliente, para actualizar los botones
    en vivo (p. ej. al terminar un refresh)."""
    return jsonify({"status": "success", "counts": load_pdf_counts()})

@app.route("/pdf-unmatched/<token>")
@login_required
def pdf_unmatched(token):
    """Detalle de las facturas sin PDF vinculado de un cliente (lo escribe el refresh)."""
    entry = load_pdf_unmatched_list(token)
    if entry is None:
        return jsonify({"status": "success", "available": False, "total": 0, "items": []})
    return jsonify({
        "status": "success",
        "available": True,
        "updatedAt": entry.get("updatedAt"),
        "total": entry.get("total", len(entry.get("items", []))),
        "items": entry.get("items", []),
    })


@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        username = request.form["username"]
        password = request.form["password"]
        user = users.get(username)
        if user and password == user.password:
            login_user(user)
            return redirect(url_for("index"))
        flash("Invalid credentials")
    return render_template("login.html")

@app.route("/logout")
@login_required
def logout():
    logout_user()
    return redirect(url_for("login"))

@app.route("/submit", methods=["POST"])
def submit():
    data = request.get_json()
    selected_id = data.get("selected_id")
    selected_name = next((c["name"] for c in companies if c["id"] == selected_id), None)

    if not selected_name:
        return jsonify({"status": "error", "message": "Invalid selection"})

    try:
        # Run task-manager.py and pass selected_name as argument
        result = subprocess.run(
            ["python", "task-manager.py", selected_name],
            capture_output=True,
            text=True,
            check=True
        )
        output = result.stdout.strip()
    except subprocess.CalledProcessError as e:
        output = f"Error running task-manager.py: {e.stderr.strip()}"

    return jsonify({"status": "success", "selected_id": selected_id, "output": output})

def read_git_state():
    """Read the live branch/version/commit from the ESG repository.

    Returns (state_dict, error_message); on success error_message is None. This is
    the authoritative source of truth for the current patron branch, so the header
    is correct even if the branch was changed outside the app.
    """
    try:
        branch_result = subprocess.run(
            ["git", "branch", "--show-current"],
            cwd=str(ESG_REPOSITORY), capture_output=True, text=True, check=False,
        )
        version_result = subprocess.run(
            ["git", "describe", "--tags", "--always", "--dirty"],
            cwd=str(ESG_REPOSITORY), capture_output=True, text=True, check=False,
        )
        commit_result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=str(ESG_REPOSITORY), capture_output=True, text=True, check=False,
        )
    except OSError as error:
        return None, f"Unable to read Git state: {error}"

    if branch_result.returncode != 0 or version_result.returncode != 0 or commit_result.returncode != 0:
        error_output = "\n".join(
            output.strip() for output in (
                branch_result.stderr, version_result.stderr, commit_result.stderr,
            ) if output and output.strip()
        )
        return None, error_output or "Unable to read the current Git version."

    return {
        "branch": branch_result.stdout.strip(),
        "version": version_result.stdout.strip(),
        "commit": commit_result.stdout.strip(),
    }, None


def load_branch_history():
    """Return the persisted branch history as a list (empty if missing/corrupt)."""
    if not BRANCH_HISTORY_FILE.is_file():
        return []
    try:
        with open(BRANCH_HISTORY_FILE, "r", encoding="utf-8") as history_file:
            history = json.load(history_file)
        return history if isinstance(history, list) else []
    except (json.JSONDecodeError, OSError) as error:
        print(f"DEBUG: Could not read branch history: {error}", flush=True)
        return []


def record_branch_history(state, source):
    """Append a branch state to the history file, only when it differs from the last
    recorded entry, so switches made outside the app are captured without spamming.
    """
    try:
        history = load_branch_history()
        last = history[-1] if history else None
        if last and last.get("commit") == state.get("commit") and last.get("branch") == state.get("branch"):
            return  # nothing changed since the last recorded entry

        history.append({
            "timestamp": datetime.now().isoformat(timespec="seconds"),
            "branch": state.get("branch"),
            "version": state.get("version"),
            "commit": state.get("commit"),
            "source": source,
        })
        with open(BRANCH_HISTORY_FILE, "w", encoding="utf-8") as history_file:
            json.dump(history, history_file, indent=2, ensure_ascii=False)
    except OSError as error:
        print(f"DEBUG: Could not record branch history: {error}", flush=True)


@app.route("/branch-status")
@login_required
def branch_status():
    """Live patron branch status, used by the frontend on page load so the header
    always shows the real branch instead of 'desconocida'."""
    state, error = read_git_state()
    if error:
        return jsonify({"status": "error", "message": error}), 500
    record_branch_history(state, source="startup")
    return jsonify({"status": "success", **state})


@app.route("/ensure-client-branch", methods=["POST"])
@login_required
def ensure_client_branch():
    """Se llama al presionar "Validate": si el cliente seleccionado tiene una rama
    de patron configurada distinta de la actual, la cambia antes de correr gera.
    Si ya está en la rama correcta, no toca el repo (evita stash/switch de más)."""
    data = request.get_json(silent=True) or {}
    selected_id = data.get("selected_id")

    selected_name = next((c["name"] for c in companies if c["id"] == selected_id), None)
    if not selected_id or not selected_name:
        return jsonify({"status": "error", "message": "Cliente inválido o no seleccionado."}), 400

    client_branches = load_client_branches()
    target_branch = (client_branches.get(selected_id) or {}).get("branch")
    if not target_branch:
        return jsonify({
            "status": "error",
            "message": f"No hay rama de patron configurada para {selected_name}.",
        }), 400

    state, error = read_git_state()
    if error:
        return jsonify({"status": "error", "message": error}), 500

    if state.get("branch") == target_branch:
        record_branch_history(state, source="validate")
        return jsonify({"status": "success", "switched": False, **state})

    if not SWITCH_PATRON_SCRIPT.is_file():
        return jsonify({"status": "error", "message": "The branch switch script was not found."}), 500

    try:
        script_result = subprocess.run(
            [
                "powershell.exe",
                "-NoProfile",
                "-ExecutionPolicy", "Bypass",
                "-File", str(SWITCH_PATRON_SCRIPT),
                "-Branch", target_branch,
            ],
            cwd=str(ESG_REPOSITORY),
            capture_output=True,
            text=True,
            check=False,
        )
    except OSError as error:
        return jsonify({"status": "error", "message": f"Unable to run branch switch: {error}"}), 500

    state, git_error = read_git_state()
    if git_error:
        combined = "\n".join(
            output.strip() for output in (script_result.stdout, script_result.stderr, git_error)
            if output and output.strip()
        )
        return jsonify({"status": "error", "message": combined or git_error}), 500

    output = "\n".join(
        output.strip() for output in (script_result.stdout, script_result.stderr)
        if output and output.strip()
    )
    status = "success" if script_result.returncode == 0 and state.get("branch") == target_branch else "error"
    record_branch_history(state, source="validate")
    response = {"status": status, "output": output, "switched": True, **state}
    if status == "error":
        response["message"] = output or "The branch switch script returned an error."
        return jsonify(response), 500
    return jsonify(response)


@app.route("/aws-sso/status")
@login_required
def aws_sso_status():
    return jsonify(aws_sso.status())


@app.route("/aws-sso/login", methods=["POST"])
@login_required
def aws_sso_login_route():
    return jsonify(aws_sso.start_login())


@app.route("/aws-sso/login/status")
@login_required
def aws_sso_login_status_route():
    return jsonify(aws_sso.login_status())


@app.route("/client-locks/<client_id>/acquire", methods=["POST"])
@login_required
def client_lock_acquire(client_id):
    """Se llama antes de abrir el EventSource de Validate/Upload/Actualizar. Si otro
    usuario ya tiene el cliente bloqueado, 409 con quién y qué flujo, y el frontend no
    abre el stream. El release ocurre en el generator de ese stream al terminar."""
    data = request.get_json(silent=True) or {}
    flow = data.get("flow") or "flow"
    ok, entry = locks.acquire(client_id, current_user.name, flow)
    if not ok:
        return jsonify({
            "status": "error",
            "message": f"Este cliente está en uso por {entry['locked_by']} ({entry['flow']}).",
            "locked_by": entry["locked_by"],
            "flow": entry["flow"],
        }), 409
    return jsonify({"status": "success", **entry})


@app.route("/client-locks/<client_id>/force-release", methods=["POST"])
@login_required
def client_lock_force_release(client_id):
    locks.force_release(client_id)
    return jsonify({"status": "success"})


@app.route("/stream-locks")
@login_required
def stream_locks():
    """SSE: cada pestaña conectada recibe el snapshot inicial y después cada
    lock/unlock en vivo, sin necesidad de refrescar la página."""
    def event_stream():
        q = locks.subscribe()
        try:
            for client_id, entry in locks.snapshot().items():
                yield f"data: {json.dumps({'type': 'lock', 'client_id': client_id, **entry})}\n\n"
            while True:
                try:
                    event = q.get(timeout=20)
                    yield f"data: {json.dumps(event)}\n\n"
                except queue.Empty:
                    yield ": keep-alive\n\n"
        finally:
            locks.unsubscribe(q)
    return Response(event_stream(), mimetype="text/event-stream")


@app.route("/stream-gera")
@login_required
def stream_gera():
    company = resolve_company(request.args.get("selectedId"))
    client_token = company["token"] if company else None
    client_id = company["id"] if company else None
    user = current_user.name

    def event_stream():
        try:
            for line in run_gera(client_token):
                if line.startswith("__GERA_METRICS__:"):
                    metrics_json = line.split(":", 1)[1].strip()
                    yield f"data: __GERA_METRICS__:{metrics_json}\n\n"
                elif line.startswith("__GERA_COMPLETED__:"):
                    # Parse the completion signal: folder_path|start_time
                    parts = line.split(":", 1)[1].strip().split("|")
                    folder_path = parts[0]
                    start_time = float(parts[1]) if len(parts) > 1 else None
                    gera_state["output_folder"] = folder_path
                    gera_state["start_time"] = start_time
                    print(f"DEBUG: Gera completed. Output folder: {folder_path}, Start time: {start_time}", flush=True)
                    yield f"data: {{'status': 'completed', 'message': 'Script completed successfully'}}\n\n"
                elif line.startswith("__GERA_ERROR__:"):
                    # Parse the error signal: folder_path|start_time|error_code
                    error_info = line.split(":", 1)[1].strip().split("|")
                    folder_path = error_info[0] if len(error_info) > 0 else None
                    start_time = float(error_info[1]) if len(error_info) > 1 else None
                    error_code = error_info[2] if len(error_info) > 2 else "Unknown"
                    gera_state["output_folder"] = folder_path
                    gera_state["start_time"] = start_time
                    print(f"DEBUG: Gera error. Output folder: {folder_path}, Start time: {start_time}, Error code: {error_code}", flush=True)
                    yield f"data: {{'status': 'error', 'message': 'Script failed with code: {error_code}'}}\n\n"
                else:
                    yield f"data: {line}\n\n"
        except Exception as e:
            print(f"DEBUG: Stream error: {str(e)}", flush=True)
            yield f"data: {{'status': 'error', 'message': 'Stream error: {str(e)}'}}\n\n"
        finally:
            if client_id:
                locks.release(client_id, user)
    return Response(event_stream(), mimetype="text/event-stream")

@app.route("/stream-refresh")
@login_required
def stream_refresh():
    client = request.args.get("selectedId")
    company_name = next((c["name"] for c in companies if c["id"] == client), None)
    user = current_user.name
    def event_stream(client, user):
        # Marca el inicio para que /download-refresh-files zippee solo los
        # reportes de error generados por esta corrida.
        refresh_state["start_time"] = time.time()
        try:
            for line in run_refresh(client, user):
                yield f"data: {line}\n\n"
        finally:
            locks.release(client, user)
    return Response(event_stream(client, user), mimetype="text/event-stream")

@app.route("/stream-upload")
@login_required
def stream_upload():
    company = resolve_company(request.args.get("selectedId"))
    client_token = company["token"] if company else None
    client_id = company["id"] if company else None
    user = current_user.name

    def event_stream():
        try:
            for line in run_upload(client_token):
                yield f"data: {line}\n\n"
        finally:
            if client_id:
                locks.release(client_id, user)
    return Response(event_stream(), mimetype="text/event-stream")

@app.route("/download-gera-files")
@login_required
def download_gera_files():
    output_folder = gera_state.get("output_folder")
    start_time = gera_state.get("start_time")

    def zip_stream():
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as zipf:
            for root, _, files in os.walk(output_folder):
                for file in files:
                    path = os.path.join(root, file)
                    if start_time is None or os.path.getmtime(path) >= start_time - 2:
                        zipf.write(path, os.path.relpath(path, output_folder))
        buffer.seek(0)
        yield from buffer

    return Response(
        stream_with_context(zip_stream()),
        mimetype="application/zip",
        headers={
            "Content-Disposition": "attachment; filename=validation_errors.zip"
        }
    )

@app.route("/download-refresh-files")
@login_required
def download_refresh_files():
    """Descarga (zip) los reportes de error generados por el último refresh:
    los de match_query (invoice_files_*.json), invoice_files_clean
    (deleted_keys_*.json) y pdf_finder (pdf_upload_*.json). Solo incluye los
    archivos modificados desde que arrancó la corrida, igual que la descarga
    de gera/just_all."""
    start_time = refresh_state.get("start_time")

    def zip_stream():
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as zipf:
            if ERRORS_DIR.is_dir():
                for path in sorted(ERRORS_DIR.iterdir()):
                    if not path.is_file():
                        continue
                    if start_time is None or path.stat().st_mtime >= start_time - 2:
                        zipf.write(str(path), path.name)
        buffer.seek(0)
        yield from buffer

    return Response(
        stream_with_context(zip_stream()),
        mimetype="application/zip",
        headers={
            "Content-Disposition": "attachment; filename=refresh_errors.zip"
        }
    )

# --------------------------------------------------------------------------- #
# Reportes de error parseados para el front (paneles expandibles)
# --------------------------------------------------------------------------- #

# Los .json cuyo nombre contenga alguno de estos tokens NUNCA se muestran.
ERROR_FILES_IGNORE = ("invalid_sites", "power_chart", "invoice_files")
# pdf_upload ya se comunica en su propio panel (#pdfReport), no se duplica acá.
ERROR_FILES_SKIP_PANEL = ("pdf_upload",)

# Etiqueta legible por prefijo de archivo (se elige el prefijo más largo que matchee).
ERROR_LABELS = {
    "invoice_files_agua": "Vinculación de facturas · Agua",
    "invoice_files_electricidad": "Vinculación de facturas · Electricidad",
    "invoice_files_gas": "Vinculación de facturas · Gas",
    "invoice_files_residuo": "Vinculación de facturas · Residuo",
    "invoice_files_combustible": "Vinculación de facturas · Combustible",
    "invoice_files_otro": "Vinculación de facturas · Otro",
    "invoice_files": "Vinculación de facturas",
    "deleted_keys": "Archivos borrados del bucket",
    "validate_invoice_electricity": "Validación de CSV · Facturas electricidad",
    "validate_invoice_gas": "Validación de CSV · Facturas gas",
    "validate_invoice_water": "Validación de CSV · Facturas agua",
    "validate_fine": "Validación de CSV · Multas (OPAH)",
    "validate": "Validación de CSV",
    "parsing_invoice_electricity": "Parseo · Facturas electricidad",
    "parsing_fine_class": "Parseo · Clasificación de multas",
    "parsing_fine": "Parseo · Multas",
    "parsing": "Parseo",
    "build_invoice_electricity": "Construcción · Facturas electricidad",
    "build_invoice_water": "Construcción · Facturas agua",
    "build_invoice_gas": "Construcción · Facturas gas",
    "build": "Construcción",
    "overlap_check": "Superposición de períodos",
    "overlap": "Superposición de períodos",
    "db_organization": "Base de datos · Organización",
    "db_site": "Base de datos · Sitios",
    "db_invoice_electricity": "Base de datos · Facturas electricidad",
    "db_invoice_water": "Base de datos · Facturas agua",
    "db_invoice_gas": "Base de datos · Facturas gas",
    "db_account_fuel": "Base de datos · Cuentas combustible",
    "db": "Base de datos",
}


def _error_file_stem(name):
    """Quita la extensión y el timestamp final (ms epoch o YYYYMMDD_HHMMSS)."""
    stem = name[:-5] if name.lower().endswith(".json") else name
    stem = re.sub(r"_\d{9,}$", "", stem)          # _1790003149249
    stem = re.sub(r"_\d{8}_\d{6}$", "", stem)      # _20260921_120542
    return stem


def _error_label(name):
    stem = _error_file_stem(name)
    for prefix in sorted(ERROR_LABELS, key=len, reverse=True):
        if stem == prefix or stem.startswith(prefix):
            return ERROR_LABELS[prefix]
    return stem.replace("_", " ").strip().capitalize()


def _error_category(name):
    stem = _error_file_stem(name)
    return stem.split("_", 1)[0] if stem else "generic"


def _collect_run_error_files(start_time):
    """Lee errors/ y devuelve los reportes de la corrida, parseados y ordenados
    del más viejo al más nuevo. Excluye los ignorados y los ya mostrados aparte."""
    files = []
    if not ERRORS_DIR.is_dir():
        return files
    entries = [p for p in ERRORS_DIR.iterdir() if p.is_file() and p.suffix.lower() == ".json"]
    for path in sorted(entries, key=lambda p: p.stat().st_mtime):
        low = path.name.lower()
        if any(tok in low for tok in ERROR_FILES_IGNORE):
            continue
        if any(low.startswith(tok) for tok in ERROR_FILES_SKIP_PANEL):
            continue
        if start_time is not None and path.stat().st_mtime < start_time - 2:
            continue
        try:
            data = json.loads(path.read_text(encoding="utf-8", errors="replace"))
        except Exception as error:  # noqa: BLE001 - un archivo malo no debe tumbar el endpoint
            data = {"__parse_error__": str(error)}
        files.append({
            "name": path.name,
            "category": _error_category(path.name),
            "label": _error_label(path.name),
            "data": data,
        })
    return files


@app.route("/run-errors/<flow>")
@login_required
def run_errors(flow):
    """Devuelve los reportes de error (parseados) de la última corrida de un flujo,
    para renderizarlos como paneles expandibles en el front."""
    if flow == "refresh":
        start_time = refresh_state.get("start_time")
    elif flow == "gera":
        start_time = gera_state.get("start_time")
    else:
        return jsonify({"status": "error", "message": "Flujo inválido."}), 400

    return jsonify({
        "status": "success",
        "flow": flow,
        "files": _collect_run_error_files(start_time),
    })


@app.route("/import-from-sheets", methods=["POST"])
@login_required
def import_from_sheets():
    """Inicia la descarga del Google Sheet en segundo plano para evitar 524s por
    timeouts del túnel/Cloudflare."""
    data = request.get_json(silent=True) or {}
    selected_id = data.get("selected_id")

    config = CLIENT_SHEETS.get(selected_id)
    company = next((c for c in companies if c["id"] == selected_id), None)
    selected_name = company["name"] if company else None
    if not config or not selected_name:
        return jsonify({"status": "error", "message": "Cliente inválido o no seleccionado."}), 400
    client_token = company["token"]

    sheet_id = (config.get("sheet_id") or "").strip()
    if not sheet_id:
        return jsonify({
            "status": "error",
            "message": f"No hay un Google Sheet configurado para {selected_name}.",
        }), 400

    job_id = uuid4().hex
    filename = f"{config['token']}.xlsx"

    with import_jobs_lock:
        import_jobs[job_id] = {
            "job_id": job_id,
            "status": "queued",
            "selected_name": selected_name,
            "filename": filename,
            "message": "Importación iniciada...",
            "file": None,
            "folder": None,
        }

    def run_import_job():
        temp_dir = tempfile.mkdtemp(prefix="sheets_import_")
        temp_path = os.path.join(temp_dir, filename)
        try:
            with import_jobs_lock:
                import_jobs[job_id]["status"] = "running"
                import_jobs[job_id]["message"] = f"Descargando {selected_name} desde Google Sheets..."

            ok, message = import_sheet(sheet_id, temp_path)
            if not ok:
                with import_jobs_lock:
                    import_jobs[job_id].update({
                        "status": "error",
                        "message": f"Error descargando el Sheet: {message}",
                    })
                return

            success, save_message, file_path = save_local_file_to_organized_folder(
                temp_path, app.config["UPLOAD_FOLDER"], client_token, filename=filename
            )
            if not success:
                with import_jobs_lock:
                    import_jobs[job_id].update({
                        "status": "error",
                        "message": f"Error guardando el archivo: {save_message}",
                    })
                return

            with import_jobs_lock:
                import_jobs[job_id].update({
                    "status": "success",
                    "message": f"{selected_name}: {save_message}",
                    "file": os.path.basename(file_path),
                    "folder": os.path.basename(os.path.dirname(file_path)),
                })
        except Exception as exc:
            with import_jobs_lock:
                import_jobs[job_id].update({
                    "status": "error",
                    "message": f"Error inesperado durante la importación: {exc}",
                })
        finally:
            shutil.rmtree(temp_dir, ignore_errors=True)

    threading.Thread(target=run_import_job, daemon=True).start()

    return jsonify({
        "status": "accepted",
        "job_id": job_id,
        "message": "Importación iniciada. Esperando respuesta del servidor...",
    }), 202


@app.route("/import-from-sheets/status/<job_id>", methods=["GET"])
@login_required
def import_from_sheets_status(job_id):
    job = import_jobs.get(job_id)
    if job is None:
        return jsonify({"status": "error", "message": "Trabajo de importación no encontrado."}), 404

    return jsonify(job), 200


@app.route("/upload-files", methods=["POST"])
@login_required
def upload_files():
    """Upload Excel files to the configured upload folder with organized subfolder structure.

    Requires selected_id so files land under UPLOAD_FOLDER/{client_token}/ — each client
    gets its own folder, so uploading for one client never archives another client's
    in-flight data (see file_handler.archive_and_prepare_folder)."""

    selected_id = request.form.get("selected_id")
    company = next((c for c in companies if c["id"] == selected_id), None)
    if not company:
        return jsonify({"status": "error", "message": "Seleccioná un cliente antes de subir archivos."}), 400
    client_token = company["token"]

    if 'files' not in request.files:
        return jsonify({"status": "error", "message": "No files provided"}), 400

    files = request.files.getlist('files')
    
    if not files or len(files) == 0:
        return jsonify({"status": "error", "message": "No files provided"}), 400
    
    uploaded_files = []
    errors = []
    
    for file in files:
        if file.filename == '':
            errors.append("Empty filename")
            continue
        
        if not allowed_file(file.filename):
            errors.append(f"{file.filename} - Invalid file type")
            continue
        
        try:
            # Use file_handler to save file to organized folder
            success, message, file_path = save_file_to_organized_folder(file, app.config['UPLOAD_FOLDER'], client_token)
            
            if success:
                uploaded_files.append(f"{file.filename} ({message})")
                print(f"DEBUG: File uploaded: {file.filename} to {file_path}", flush=True)
            else:
                errors.append(f"{file.filename} - {message}")
                print(f"DEBUG: Error uploading {file.filename}: {message}", flush=True)
        except Exception as e:
            errors.append(f"{file.filename} - {str(e)}")
            print(f"DEBUG: Error uploading {file.filename}: {str(e)}", flush=True)
    
    if uploaded_files:
        return jsonify({
            "status": "success",
            "message": f"Successfully uploaded {len(uploaded_files)} file(s)",
            "uploaded_files": uploaded_files,
            "errors": errors if errors else None
        }), 200
    else:
        return jsonify({
            "status": "error",
            "message": "No files were uploaded",
            "errors": errors
        }), 400

if __name__ == "__main__":
    app.run(debug=True)
