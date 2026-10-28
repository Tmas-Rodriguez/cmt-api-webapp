"""Planilla Patrón v1.5 -> v1.6, run by "Validar" before gera.py (every client but ICBC).

gera.py validates the newest providers/<cliente>/csv_<alias>_<YYYYMMDD>/ folder. If that folder
is still a v1.5 set (datos_e.csv with impuestos_tasas), prepare_latest() turns it into v1.6 in
place: the original goes to providers/<cliente>/old/<carpeta>_v15_original/ and the converted
set takes its name. A folder with only the .xlsx (an import from Sheets) is first split into
CSVs with gera's own excel_to_csv, as gera would do. Folders already in v1.6 are left alone.

Rules (agreed for Alsea on 2026-10-27, impuestos_tasas -> otros on 2026-10-28); the standalone
copy on the Desktop (convertir_planilla_v15_a_v16.py) follows the same ones:
- datos_e: impuestos_tasas -> otros (cargos_impositivos stays empty). datos_a / datos_g keep it.
- Columns in EsgCmt-API's v1.6 parser order; the v1.6 columns v1.5 lacks go empty.
- datos_opah penalties -> their invoice's column (full key, else the period when a single
  invoice has it): 1 -> datos_e.recargo_exceso_potencia, 2 -> datos_e.recargo_reactiva,
  3 -> datos_e.recargo_mora, 4 -> datos_g.recargo_mora, 5 (water mora) -> added to datos_a.otros.
  Several on one invoice are summed. The reactive penalty's cos φ -> datos_e.cosfi; kW
  excedidos have no v1.6 column and are dropped.
- saldo_anterior always empty (carried-over balances: CMT v1.6 would add them to the bruto).
- datos_opah.csv / opah.csv empty files, as EsgCmt-API's v1.6 exports. Other tables as they are.
- UTF-8 without BOM, every field quoted, CRLF (also after the last line).
"""
import csv
import os
import re
import shutil
import subprocess
from datetime import datetime
from pathlib import Path

SKIP_TOKENS = {"icbc"}   # ICBC stays on v1.5

CHARGES = ["contribuciones", "cargos_impositivos", "tasas", "financiacion", "percepcion_iva",
           "recargo_reactiva", "bonificacion_reactiva", "recargo_exceso_potencia", "otros_costos_consumo",
           "costo_total_consumo", "recargo_mora", "saldo_anterior"]
HEAD = ["id_sitio", "proveedor", "cuenta", "fecha_anterior", "fecha_actual", "fecha_emision",
        "fecha_vencimiento", "num_factura"]
V16_HEADERS = {
    "datos_e": HEAD + ["uso", "uso_2", "uso_3", "uso_r", "costo_1", "costo_2", "costo_3", "costo_neto",
                       "cosfi", "tgfi", "costo_fijo", "impuestos", "IVA", "otros", "pc1", "pc2", "pc3",
                       "pd1", "pd2", "pd3", "costo_pc1", "costo_pc2", "costo_pc3", "costo_pd1", "costo_pd2",
                       "costo_pd3", "total_a_pagar"] + CHARGES,
    "datos_a": HEAD + ["uso", "costo_neto", "costo_fijo", "impuestos_tasas", "impuestos", "IVA", "otros"],
    "datos_g": HEAD + ["uso", "uso_9300", "costo_neto", "costo_fijo", "impuestos_tasas", "impuestos", "IVA",
                       "otros"] + CHARGES,
}
KEY = ["id_sitio", "proveedor", "cuenta", "fecha_anterior", "fecha_actual", "num_factura"]
PERIOD = KEY[:5]
PENALTY_TARGET = {
    1: ("datos_e", "recargo_exceso_potencia"),
    2: ("datos_e", "recargo_reactiva"),
    3: ("datos_e", "recargo_mora"),
    4: ("datos_g", "recargo_mora"),
    5: ("datos_a", "otros"),
}
PENALTY_NAME = {1: "exceso de potencia", 2: "reactiva", 3: "mora luz", 4: "mora gas", 5: "mora agua"}
FOLDER_RE = re.compile(r"csv_([a-zA-Z]+)_(\d{8})$")   # the same pattern gera.py looks for


def fmt(v: float) -> str:
    """A number as the exports write it: integers without .0, the rest in their shortest form."""
    if v.is_integer() and abs(v) < 1e16:
        return str(int(v))
    return repr(v)


def num(s: str) -> float:
    return float(s) if s and s.strip() else 0.0


def read_csv(path: Path):
    """(header, rows as dicts); an empty file gives ([], [])."""
    with open(path, encoding="utf-8-sig", newline="") as f:
        rows = [r for r in csv.reader(f) if any(c.strip() for c in r)]
    if not rows:
        return [], []
    header = rows[0]
    return header, [dict(zip(header, r + [""] * (len(header) - len(r)))) for r in rows[1:]]


def write_csv(path: Path, header: list, rows: list):
    with open(path, "w", encoding="utf-8", newline="") as f:
        w = csv.writer(f, quoting=csv.QUOTE_ALL, lineterminator="\r\n")
        w.writerow(header)
        for r in rows:
            w.writerow([r.get(c, "") for c in header])


def is_v15_set(folder: Path) -> bool:
    e = folder / "datos_e.csv"
    if not e.exists():
        return False
    header, _ = read_csv(e)
    return "impuestos_tasas" in header and "cargos_impositivos" not in header


def convert(src: Path, dst: Path) -> list:
    """Write the v1.6 version of the v1.5 set in src into dst; returns the log lines."""
    log = [f"Origen: {src}", f"Destino: {dst}", ""]
    dst.mkdir(parents=True, exist_ok=True)
    tables, headers = {}, {}
    for t in V16_HEADERS:
        p = src / f"{t}.csv"
        if p.exists():
            headers[t], tables[t] = read_csv(p)

    by_key, by_period = {}, {}
    for t, rows in tables.items():
        for i, r in enumerate(rows):
            by_key.setdefault((t, tuple(r.get(c, "").strip() for c in KEY)), []).append(i)
            by_period.setdefault((t, tuple(r.get(c, "").strip() for c in PERIOD)), []).append(i)

    adds, cosfi, moved, lost, kw_lost = {}, {}, {}, [], 0
    opah = src / "datos_opah.csv"
    if opah.exists():
        _, penalties = read_csv(opah)
        for p in penalties:
            try:
                m = int(float(p.get("id_multa", "") or 0))
            except ValueError:
                m = 0
            if m not in PENALTY_TARGET:
                lost.append(f"id_multa desconocido {p.get('id_multa')!r}: {p}")
                continue
            table, col = PENALTY_TARGET[m]
            if table not in tables:
                lost.append(f"{PENALTY_NAME[m]} sin tabla {table}: {p}")
                continue
            hits = by_key.get((table, tuple(p.get(c, "").strip() for c in KEY)), [])
            if len(hits) != 1:
                per = by_period.get((table, tuple(p.get(c, "").strip() for c in PERIOD)), [])
                hits = per if len(per) == 1 else []
            if not hits:
                lost.append(f"{PENALTY_NAME[m]} sin factura: " + ", ".join(f"{c}={p.get(c, '')}" for c in KEY)
                            + f", monto={p.get('monto', '')}")
                continue
            row = hits[0]
            per_row = adds.setdefault((table, row), {})
            per_row[col] = per_row.get(col, 0.0) + num(p.get("monto", ""))
            moved[m] = moved.get(m, 0) + 1
            adic = (p.get("dato_adic") or "").strip()
            if m == 2 and adic:
                cur = cosfi.get(row)
                cosfi[row] = adic if cur is None or num(adic) > num(cur) else cur
            if m == 1 and adic:
                kw_lost += 1

    saldos = taxes = 0
    for t, rows in tables.items():
        cols = ["otros" if (t == "datos_e" and c == "impuestos_tasas") else c for c in headers[t]]
        header = V16_HEADERS[t] + [c for c in cols if c not in V16_HEADERS[t]]
        out = []
        for i, r in enumerate(rows):
            n = dict(r)
            if t == "datos_e" and "impuestos_tasas" in n:
                tax = n.pop("impuestos_tasas")
                if tax.strip():
                    taxes += 1
                    n["otros"] = fmt(num(n["otros"]) + num(tax)) if n.get("otros", "").strip() else tax
            if n.get("saldo_anterior", "").strip():
                saldos += 1
            n["saldo_anterior"] = ""
            for col, v in adds.get((t, i), {}).items():
                n[col] = fmt(round(num(n.get(col, "")) + v, 6) if col == "otros" else num(n.get(col, "")) + v)
            if t == "datos_e" and i in cosfi:
                n["cosfi"] = cosfi[i]
            out.append(n)
        write_csv(dst / f"{t}.csv", header, out)
        log.append(f"{t}.csv: {len(out)} facturas, {len(header)} columnas (v1.6)")

    for name in ("datos_opah.csv", "opah.csv"):
        (dst / name).write_bytes(b"\r\n")
    copied = 0
    for f in sorted(src.iterdir()):
        # the other tables as they are, and files like Apollo's marker; the .xlsx stays with the original
        if (not f.is_file() or f.stem in V16_HEADERS or f.name in ("datos_opah.csv", "opah.csv")
                or f.suffix.lower() == ".xlsx" or f.name == "conversion_log.txt"):
            continue
        shutil.copyfile(f, dst / f.name)
        copied += 1
    log.append(f"datos_opah.csv y opah.csv: vacíos (v1.6). Otros {copied} archivos copiados sin cambios.")
    log.append(f"impuestos_tasas de electricidad -> datos_e.otros: {taxes}")
    log.append("Multas pasadas a su factura:")
    for m in sorted(PENALTY_TARGET):
        t, c = PENALTY_TARGET[m]
        log.append(f"  {PENALTY_NAME[m]:20s} -> {t}.{c}: {moved.get(m, 0)}")
    log.append(f"cos φ de reactiva -> datos_e.cosfi: {len(cosfi)}")
    log.append(f"kW excedidos descartados (sin columna en v1.6): {kw_lost}")
    if saldos:
        log.append(f"saldo_anterior: {saldos} valores quedaron vacíos (los saldos no se exportan)")
    if lost:
        log.append(f"ATENCIÓN: {len(lost)} multa(s) sin factura, no se pasaron:")
        log.extend("  " + x for x in lost)
    (dst / "conversion_log.txt").write_text("\n".join(log) + "\n", encoding="utf-8")
    return log


def latest_folder(client_dir: Path):
    """The folder gera.py will validate: the newest csv_<alias>_<YYYYMMDD> (same rule as its
    find_latest_csv_folder)."""
    found = []
    for item in client_dir.iterdir():
        m = FOLDER_RE.match(item.name)
        if item.is_dir() and m:
            try:
                found.append((datetime.strptime(m.group(2), "%Y%m%d"), item))
            except ValueError:
                continue
    if not found:
        return None
    found.sort(key=lambda x: x[0], reverse=True)
    return found[0][1]


def _split_xlsx(folder: Path, esg_repository: Path):
    """An import from Sheets brings only the .xlsx: split it into CSVs with gera's own
    excel_to_csv (gera then sees the CSVs and doesn't split it again). Yields excel_to_csv's
    lines as it goes (a big planilla takes minutes). The split runs in a scratch folder next to
    it and the CSVs move in only at the end, so the folder is never seen half split."""
    scratch = folder.parent / f".{folder.name}.split_tmp"
    shutil.rmtree(scratch, ignore_errors=True)
    scratch.mkdir()
    try:
        for x in sorted(folder.glob("*.xlsx")):
            shutil.copy2(x, scratch / x.name)
        code = ("import sys, gera\n"
                "from pathlib import Path\n"
                "for x in sorted(Path(sys.argv[1]).glob('*.xlsx')):\n"
                "    gera.excel_to_csv(str(x))\n")
        env = os.environ.copy()
        env["PYTHONIOENCODING"] = "utf-8"
        proc = subprocess.Popen(["python", "-u", "-c", code, str(scratch)], cwd=str(esg_repository),
                                stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                                encoding="utf-8", errors="replace", env=env)
        tail = []
        for line in proc.stdout:
            line = line.rstrip()
            tail = (tail + [line])[-10:]
            if line.startswith("Solapa "):          # "Solapa 'datos_e' exportada a '<path>'"
                yield "   " + line.split(" a '")[0]
        if proc.wait() != 0:
            raise RuntimeError("No se pudo pasar el .xlsx a CSV: " + " | ".join(tail)[-500:])
        for csv_file in scratch.glob("*.csv"):
            os.replace(csv_file, folder / csv_file.name)
    finally:
        shutil.rmtree(scratch, ignore_errors=True)


def prepare_latest(client_token, providers_dir: Path, esg_repository: Path):
    """Yield log lines; leaves the client's newest folder in v1.6 (raises on failure, with the
    original folder untouched)."""
    if not client_token or client_token in SKIP_TOKENS:
        return
    client_dir = providers_dir / client_token
    if not client_dir.is_dir():
        return
    folder = latest_folder(client_dir)
    if folder is None:
        return
    files = [f for f in folder.iterdir() if f.is_file()]
    if files and all(f.suffix.lower() == ".xlsx" for f in files):
        yield f"📄 {folder.name}: solo trae el .xlsx, lo paso a CSV (con una planilla grande tarda unos minutos)..."
        yield from _split_xlsx(folder, esg_repository)
    if not is_v15_set(folder):
        yield f"✅ {folder.name} ya está en v1.6 (o no tiene datos_e en v1.5): se valida tal cual"
        return

    yield f"🔄 {folder.name} está en v1.5: la convierto a v1.6..."
    tmp = client_dir / f".{folder.name}.v16_tmp"
    shutil.rmtree(tmp, ignore_errors=True)
    try:
        log = convert(folder, tmp)
    except Exception:
        shutil.rmtree(tmp, ignore_errors=True)
        raise
    old_dir = client_dir / "old"
    old_dir.mkdir(exist_ok=True)
    target = old_dir / f"{folder.name}_v15_original"
    if target.exists():
        target = old_dir / f"{folder.name}_v15_original_{datetime.now():%H%M%S}"
    shutil.move(str(folder), str(target))
    try:
        os.rename(tmp, folder)
    except OSError:
        shutil.move(str(target), str(folder))   # put the original back: nothing changed
        raise
    for line in log[3:]:
        yield "   " + line
    yield f"✅ Convertida. El original v1.5 quedó en old/{target.name}"
