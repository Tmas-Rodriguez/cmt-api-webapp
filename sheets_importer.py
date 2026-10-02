"""Exporta una planilla de Google Sheets como .xlsx a una ruta destino.

Se ejecuta con el Python del venv de data-automation (el que tiene google-auth /
requests), invocado desde sheets_runner.py.

Uso:
    python sheets_importer.py <sheet_id> <dest_xlsx_path>

Imprime "OK: <ruta>" y sale con 0 si funcionó; "ERROR: <detalle>" y código != 0
si falló. La service account debe tener al menos permiso de lectura sobre el Sheet.

Nota: se usa la URL de export web de Google (docs.google.com/.../export?format=xlsx)
autenticada con el token de la service account, en vez de la Drive API
(files().export), porque esta última tiene un tope de 10 MB para el archivo
exportado y estas planillas lo superan. La URL de export web devuelve el .xlsx
completo, idéntico al "Descargar como .xlsx" manual.
"""
import sys
from pathlib import Path

import requests
from google.oauth2 import service_account
from google.auth.transport.requests import Request

SERVICE_ACCOUNT_FILE = (
    r"C:\Users\cmt\Documents\Repsitories\data-automation\data_automation-esg"
    r"\MismatchesAPP\core\drive_service\service_account_secrets.json"
)
SCOPES = ["https://www.googleapis.com/auth/drive.readonly"]
EXPORT_URL = "https://docs.google.com/spreadsheets/d/{sheet_id}/export?format=xlsx"


def export_sheet_as_xlsx(sheet_id, dest_path):
    creds = service_account.Credentials.from_service_account_file(
        SERVICE_ACCOUNT_FILE, scopes=SCOPES
    )
    creds.refresh(Request())

    url = EXPORT_URL.format(sheet_id=sheet_id)
    response = requests.get(
        url,
        headers={"Authorization": "Bearer " + creds.token},
        allow_redirects=True,
        timeout=300,
    )
    response.raise_for_status()

    # Un .xlsx es un ZIP: empieza con "PK". Si no, la respuesta suele ser una página
    # HTML de error/permiso (p. ej. la SA no tiene acceso al Sheet).
    if response.content[:2] != b"PK":
        raise RuntimeError(
            f"La respuesta no es un .xlsx (HTTP {response.status_code}, "
            f"content-type {response.headers.get('content-type')}). "
            "¿La service account tiene acceso de lectura al Sheet?"
        )

    dest = Path(dest_path)
    dest.parent.mkdir(parents=True, exist_ok=True)
    with open(dest, "wb") as output_file:
        output_file.write(response.content)
    return dest


def main():
    if len(sys.argv) != 3:
        print("ERROR: uso: sheets_importer.py <sheet_id> <dest_xlsx_path>")
        return 2

    sheet_id, dest_path = sys.argv[1], sys.argv[2]
    try:
        dest = export_sheet_as_xlsx(sheet_id, dest_path)
    except Exception as error:  # noqa: BLE001 - reportamos cualquier fallo por stdout
        print(f"ERROR: {error}")
        return 1
    print(f"OK: {dest}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
