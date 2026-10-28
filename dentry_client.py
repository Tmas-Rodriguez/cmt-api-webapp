"""Llamadas internas de la webapp a la app de data entry (Docker en este mismo equipo).

Las rutas /internal/cmt/* del data entry no usan sesión de usuario: piden un token Bearer
firmado con CMT_SSO_SECRET (mismo valor en ambos .env), con salt "cmt-export", un alcance
(`scope`) y 60 segundos de vida.
"""

import json
import os
import urllib.error
import urllib.request

DATA_ENTRY_URL = os.environ.get("DATA_ENTRY_INTERNAL_URL", "http://127.0.0.1:5500")
EXPORT_SALT = "cmt-export"


def request(path, scope, method="GET", timeout=30):
    """Bytes de la respuesta de `path` en el data entry. Lanza RuntimeError con un mensaje legible."""
    from itsdangerous import URLSafeTimedSerializer

    import dentry_auth

    secret = dentry_auth.env_value(dentry_auth.SSO_ENV_KEY)
    if not secret:
        raise RuntimeError(f"Falta {dentry_auth.SSO_ENV_KEY} en el .env de la webapp.")
    token = URLSafeTimedSerializer(secret, salt=EXPORT_SALT).dumps({"scope": scope})
    req = urllib.request.Request(
        f"{DATA_ENTRY_URL}{path}",
        method=method,
        data=b"" if method == "POST" else None,
        headers={"Authorization": f"Bearer {token}"},
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as response:
            return response.read()
    except urllib.error.HTTPError as error:
        try:   # our own refusals carry {"message": ...}
            message = json.loads(error.read() or b"{}").get("message")
        except (ValueError, AttributeError, OSError):
            message = None
        if message and error.code in (400, 409):
            raise RuntimeError(message) from error
        hint = " (¿el data entry tiene la versión con las rutas internas? Hay que reconstruirlo.)" if error.code in (403, 404) else ""
        raise RuntimeError(f"El data entry respondió {error.code}{hint}") from error
    except (urllib.error.URLError, OSError) as error:
        raise RuntimeError(f"No se pudo conectar con el data entry en {DATA_ENTRY_URL}: {error}") from error
