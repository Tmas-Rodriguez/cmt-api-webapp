"""
Estado y acciones de sesión SSO de AWS para el pill/botón "arriba a la derecha".

Mismo patrón que PipelineMonitor (data-automation/data_automation-esg/PipelineMonitor):
- status(): `aws sts get-caller-identity --profile <profile>` cacheado unos segundos.
- start_login(): `aws sso login --profile <sso_profile> --no-browser --use-device-code`
  en background, parseando stdout para la URL + código de device.

Los comandos `match_query`/`invoice_files_clean`/`bucket_files_sync` (Actualizar, Upload)
usan el profile de credenciales "Tomi" (ver EsgCmt-API/refresh_sso_creds.py), que se
refresca a partir de la sesión SSO del profile "Tomi-sso" una vez logueado.
"""

import re
import subprocess
import sys
import threading
import time
from pathlib import Path

CREDS_PROFILE = "Tomi"
SSO_PROFILE = "Tomi-sso"
STATUS_TTL_SECONDS = 5

ESG_REPOSITORY = Path(r"C:\Users\cmt\Documents\Repsitories\EsgCmt-API")

_URL_RE = re.compile(r"https://\S+/start/#/device\?user_code=\S+")
_CODE_RE = re.compile(r"[A-Z]{4}-[A-Z]{4}")

_lock = threading.Lock()
_status_cache = {"checked_at": 0.0, "active": False}
_login_state = {"phase": "idle", "url": None, "code": None, "message": None}


def _caller_identity_active(profile):
    try:
        result = subprocess.run(
            ["aws", "sts", "get-caller-identity", "--profile", profile, "--output", "json"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        return result.returncode == 0
    except (subprocess.SubprocessError, OSError):
        return False


def status(force=False):
    """Estado cacheado (~5s) de la sesión, para no golpear `aws sts` en cada poll del front."""
    with _lock:
        now = time.time()
        if force or (now - _status_cache["checked_at"]) > STATUS_TTL_SECONDS:
            _status_cache["active"] = _caller_identity_active(CREDS_PROFILE)
            _status_cache["checked_at"] = now
        return {"active": _status_cache["active"]}


def login_status():
    with _lock:
        return dict(_login_state)


def _refresh_credentials():
    """Reusa EsgCmt-API/refresh_sso_creds.py: baja el access token SSO recién obtenido
    y escribe las credenciales temporales del profile "Tomi" en ~/.aws/credentials."""
    if str(ESG_REPOSITORY) not in sys.path:
        sys.path.insert(0, str(ESG_REPOSITORY))
    import refresh_sso_creds
    refresh_sso_creds.update_credentials_file()


def _run_login():
    with _lock:
        _login_state.update(phase="starting", url=None, code=None, message=None)

    try:
        process = subprocess.Popen(
            ["aws", "sso", "login", "--profile", SSO_PROFILE, "--no-browser", "--use-device-code"],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
        )
    except OSError as e:
        with _lock:
            _login_state.update(phase="failed", message=f"No se pudo iniciar 'aws sso login': {e}")
        return

    for line in process.stdout:
        with _lock:
            if _login_state["url"] is None:
                url_match = _URL_RE.search(line)
                if url_match:
                    _login_state["url"] = url_match.group(0)
            if _login_state["code"] is None:
                code_match = _CODE_RE.search(line)
                if code_match:
                    _login_state["code"] = code_match.group(0)
            if _login_state["url"] and _login_state["phase"] == "starting":
                _login_state["phase"] = "awaiting"
    process.stdout.close()
    return_code = process.wait()

    if return_code != 0:
        with _lock:
            _login_state.update(phase="failed", message="El login SSO falló o se canceló.")
        return

    with _lock:
        _login_state.update(phase="refreshing", message="Actualizando credenciales...")

    try:
        _refresh_credentials()
    except Exception as e:
        with _lock:
            _login_state.update(phase="failed", message=f"Login OK pero no se pudieron refrescar credenciales: {e}")
        return

    status(force=True)
    with _lock:
        _login_state.update(phase="completed", message="Sesión SSO activa.")


def start_login():
    with _lock:
        if _login_state["phase"] in ("starting", "awaiting", "refreshing"):
            return dict(_login_state)
    threading.Thread(target=_run_login, daemon=True).start()
    with _lock:
        return dict(_login_state)
