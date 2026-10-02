import subprocess
from pathlib import Path

# Python del venv de data-automation (tiene google-api-python-client / google-auth).
# Mismo venv que usa deviations_runner.py.
DATA_AUTOMATION_VENV_PYTHON = Path(
    r"C:\Users\cmt\Documents\Repsitories\data-automation\data_automation-esg"
    r"\data-automation-venv\Scripts\python.exe"
)
SHEETS_IMPORTER_SCRIPT = Path(__file__).resolve().parent / "sheets_importer.py"


def import_sheet(sheet_id, dest_path):
    """Descarga el Sheet `sheet_id` como .xlsx en `dest_path`.

    Devuelve (ok: bool, message: str). No lanza excepciones: cualquier fallo se
    reporta en `message`.
    """
    if not DATA_AUTOMATION_VENV_PYTHON.is_file():
        return False, f"No se encontró el Python del venv de data-automation: {DATA_AUTOMATION_VENV_PYTHON}"
    if not SHEETS_IMPORTER_SCRIPT.is_file():
        return False, f"No se encontró sheets_importer.py: {SHEETS_IMPORTER_SCRIPT}"

    try:
        result = subprocess.run(
            [str(DATA_AUTOMATION_VENV_PYTHON), str(SHEETS_IMPORTER_SCRIPT), sheet_id, str(dest_path)],
            capture_output=True,
            text=True,
            encoding="utf-8",
            check=False,
        )
    except OSError as error:
        return False, f"No se pudo ejecutar la importación: {error}"

    output = (result.stdout or "").strip()
    if result.returncode == 0:
        return True, output or "Importación completada."

    error_detail = output or (result.stderr or "").strip()
    return False, error_detail or f"La importación falló con código {result.returncode}"
