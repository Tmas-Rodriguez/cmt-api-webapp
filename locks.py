"""
Lock por cliente + broadcast en tiempo real (SSE), para que dos personas no corran
Validate/Upload/Actualizar sobre el mismo cliente (mismo DB de producción) a la vez.

- acquire()/release()/force_release() son las únicas formas de tocar `_locks`.
- Cada cambio se empuja a todas las colas suscriptas (una por pestaña de navegador
  conectada a /stream-locks), así el resto de las pestañas se entera sin refrescar.
- Expiración automática (LOCK_TTL_SECONDS): si el proceso que sostenía un lock murió
  (crash, reinicio del server) sin liberar, cualquier lectura posterior a los ~30 min
  lo trata como libre. "Forzar desbloqueo" cubre el caso de necesitarlo antes de eso.

Nota (documentada, no resuelta acá): .env.script y el contenedor de Mongo local siguen
siendo recursos compartidos a nivel repo. Este lock evita que DOS personas toquen el
MISMO cliente a la vez, pero no aísla del todo dos clientes DISTINTOS corriendo en el
mismo instante (ver plan). Fuera de alcance de este cambio.
"""

import queue
import threading
import time

LOCK_TTL_SECONDS = 30 * 60

_lock_guard = threading.Lock()
_locks = {}          # client_id -> {"locked_by", "flow", "started_at"}
_subscribers = []     # list[queue.Queue] — una por pestaña conectada a /stream-locks


def _is_expired(entry):
    return (time.time() - entry["started_at"]) > LOCK_TTL_SECONDS


def _broadcast(event):
    with _lock_guard:
        subs = list(_subscribers)
    for q in subs:
        try:
            q.put_nowait(event)
        except queue.Full:
            pass


def snapshot():
    """{client_id: {locked_by, flow, started_at}} de los locks vigentes (no expirados)."""
    with _lock_guard:
        active = {}
        expired = []
        for client_id, entry in _locks.items():
            if _is_expired(entry):
                expired.append(client_id)
            else:
                active[client_id] = dict(entry)
        for client_id in expired:
            _locks.pop(client_id, None)
        return active


def acquire(client_id, user, flow):
    """True si se pudo tomar (estaba libre o expirado); False si ya lo tiene otro."""
    with _lock_guard:
        entry = _locks.get(client_id)
        if entry and not _is_expired(entry) and entry["locked_by"] != user:
            return False, entry
        _locks[client_id] = {"locked_by": user, "flow": flow, "started_at": time.time()}
        new_entry = dict(_locks[client_id])
    _broadcast({"type": "lock", "client_id": client_id, **new_entry})
    return True, new_entry


def release(client_id, user):
    """Libera solo si el mismo `user` lo tiene (una corrida ajena no debería poder pisarlo)."""
    with _lock_guard:
        entry = _locks.get(client_id)
        if not entry or entry["locked_by"] != user:
            return
        _locks.pop(client_id, None)
    _broadcast({"type": "unlock", "client_id": client_id})


def force_release(client_id):
    with _lock_guard:
        _locks.pop(client_id, None)
    _broadcast({"type": "unlock", "client_id": client_id})


def subscribe():
    q = queue.Queue(maxsize=100)
    with _lock_guard:
        _subscribers.append(q)
    return q


def unsubscribe(q):
    with _lock_guard:
        if q in _subscribers:
            _subscribers.remove(q)
