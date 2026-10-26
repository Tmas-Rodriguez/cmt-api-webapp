"""Usuarios de la webapp = admins activos de la app de data entry.

La única fuente de verdad es la tabla app.users de la base `manual_data_entry`
(Postgres). Acá no se guarda ningún usuario ni contraseña: se valida contra esa tabla en
cada login (mismo hash de werkzeug que usa el data entry) y se revalida cada
CACHE_TTL segundos, así que un admin dado de baja, desactivado o degradado pierde el
acceso a los pocos segundos.

Configuración: DATA_ENTRY_DB_URL (variable de entorno o archivo .env junto a este
módulo). Conviene un rol de Postgres de solo lectura sobre app.users (ver .env.example).
"""

import os
import re
import threading
import time
from pathlib import Path

import psycopg
from werkzeug.security import check_password_hash, generate_password_hash

ENV_FILE = Path(__file__).with_name(".env")
ENV_KEY = "DATA_ENTRY_DB_URL"
CACHE_TTL_SECONDS = 30
CONNECT_TIMEOUT_SECONDS = 5

_ADMIN_QUERY = (
    "SELECT username, password_hash FROM app.users "
    "WHERE username = %s AND role = 'admin' AND active"
)
# Se compara contra un hash falso cuando el usuario no existe, para que el tiempo de
# respuesta no revele qué usuarios existen.
_DUMMY_HASH = generate_password_hash("not-a-real-password")

_cache = {}  # username -> (monotonic_ts, row | None)
_cache_lock = threading.Lock()


class AuthUnavailable(Exception):
    """No se pudo consultar la base de usuarios (sin config, caída o credenciales)."""


def _db_url():
    url = os.environ.get(ENV_KEY)
    if not url and ENV_FILE.is_file():
        for line in ENV_FILE.read_text(encoding="utf-8").splitlines():
            key, sep, value = line.partition("=")
            if sep and key.strip() == ENV_KEY:
                url = value.strip().strip('"').strip("'")
                break
    if not url:
        raise AuthUnavailable(f"Falta {ENV_KEY} (variable de entorno o .env).")
    # URLs estilo SQLAlchemy (postgresql+psycopg2://) -> formato libpq.
    return re.sub(r"^postgres(?:ql)?\+\w+://", "postgresql://", url)


def _fetch_admin(username):
    """Fila {username, password_hash} del admin activo, o None. Lanza AuthUnavailable."""
    try:
        with psycopg.connect(_db_url(), connect_timeout=CONNECT_TIMEOUT_SECONDS) as conn:
            row = conn.execute(_ADMIN_QUERY, (username,)).fetchone()
    except AuthUnavailable:
        raise
    except Exception as error:  # noqa: BLE001 - cualquier fallo de conexión/consulta
        raise AuthUnavailable(str(error)) from error
    return {"username": row[0], "password_hash": row[1]} if row else None


def authenticate(username, password):
    """Devuelve el username si es un admin activo con esa contraseña; si no, None.

    Lanza AuthUnavailable si la base de usuarios no responde (no se confunde con
    "credenciales inválidas").
    """
    if not isinstance(username, str) or not username or "\x00" in username or "\x00" in (password or ""):
        return None
    row = _fetch_admin(username)
    ok = check_password_hash(row["password_hash"] if row else _DUMMY_HASH, password or "")
    if row and ok:
        with _cache_lock:
            _cache[username] = (time.monotonic(), row)
        return row["username"]
    return None


def get_admin(username):
    """Admin activo por username (para el user_loader), cacheado CACHE_TTL_SECONDS.

    Si la base no responde se sigue usando la última respuesta conocida; sin ninguna,
    devuelve None (sesión no válida).
    """
    if not isinstance(username, str) or not username:
        return None
    now = time.monotonic()
    with _cache_lock:
        cached = _cache.get(username)
    if cached and now - cached[0] < CACHE_TTL_SECONDS:
        return cached[1]
    try:
        row = _fetch_admin(username)
    except AuthUnavailable:
        return cached[1] if cached else None
    with _cache_lock:
        _cache[username] = (now, row)
    return row


# ---------------------------------------------------------------------------
# SSO desde el data entry (iframe de /admin/cmt)
# ---------------------------------------------------------------------------
# El data entry firma {"u": username, "j": id} con CMT_SSO_SECRET (mismo valor acá) y lo
# pasa en /sso?token=...; vale SSO_TOKEN_MAX_AGE_SECONDS y una sola vez. Aun con token
# válido se vuelve a comprobar en la base que siga siendo admin activo.

SSO_ENV_KEY = "CMT_SSO_SECRET"
SSO_SALT = "cmt-sso"
SSO_TOKEN_MAX_AGE_SECONDS = 60

_used_tokens = {}  # jti -> monotonic expiry
_used_lock = threading.Lock()


def _env_value(key):
    value = os.environ.get(key)
    if not value and ENV_FILE.is_file():
        for line in ENV_FILE.read_text(encoding="utf-8").splitlines():
            name, sep, rest = line.partition("=")
            if sep and name.strip() == key:
                value = rest.strip().strip('"').strip("'")
                break
    return value


env_value = _env_value  # público: lo usa icbc_runner para leer el secreto compartido


def consume_sso_token(token):
    """Username del admin dueño del token, o None (inválido, vencido, usado o sin SSO).

    Lanza AuthUnavailable si no se puede consultar la base de usuarios.
    """
    from itsdangerous import BadData, URLSafeTimedSerializer

    secret = _env_value(SSO_ENV_KEY)
    if not secret or not token:
        return None
    try:
        data = URLSafeTimedSerializer(secret, salt=SSO_SALT).loads(
            token, max_age=SSO_TOKEN_MAX_AGE_SECONDS
        )
        username, jti = data["u"], data["j"]
    except (BadData, KeyError, TypeError):
        return None

    now = time.monotonic()
    with _used_lock:
        for key in [k for k, exp in _used_tokens.items() if exp < now]:
            del _used_tokens[key]
        if jti in _used_tokens:
            return None
        _used_tokens[jti] = now + SSO_TOKEN_MAX_AGE_SECONDS * 2

    row = _fetch_admin(username)
    if not row:
        return None
    with _cache_lock:
        _cache[username] = (time.monotonic(), row)
    return row["username"]
