# Network.py
import os
import re
from datetime import datetime
from typing import Optional
import pydicom
from PySide6.QtCore import QThread, Signal

from config import (
    MAX_WALK_DEPTH, SCANDIDOS_TOMO_SIGNATURE, TOMO_DEFAULT_ENERGY, log, DashboardData
)

def iter_files(root_path: str, max_depth: int = MAX_WALK_DEPTH):
    root_path = os.path.abspath(root_path)
    for current, dirs, files in os.walk(root_path):
        if current[len(root_path):].count(os.sep) >= max_depth:
            dirs.clear()
        for file_name in files:
            yield current, file_name

def check_dicom_and_pdf(folder_path: Optional[str], read_energy: bool = False):
    if not folder_path: return False, False, None, None
    dcm_date, energy, latest_pdf = None, None, None

    try:
        for root, file_name in iter_files(folder_path):
            full_path = os.path.join(root, file_name)
            lower = file_name.lower()
            if lower.endswith(".dcm"):
                if dcm_date is None:
                    try: dcm_date = datetime.fromtimestamp(os.path.getmtime(full_path))
                    except OSError: pass
                if read_energy and energy is None and "RP" in file_name.upper():
                    try:
                        ds = pydicom.dcmread(full_path, stop_before_pixels=True)
                        b = ds.BeamSequence[0]
                        e_val = str(b.ControlPointSequence[0].NominalBeamEnergy)
                        m = "FFF" if "FFF" in str(getattr(b.PrimaryFluenceModeSequence[0], "FluenceModeID", "")) else "X"
                        energy = f"{e_val}{m}"
                    except Exception: pass
            elif lower.endswith(".pdf"):
                try:
                    p_date = datetime.fromtimestamp(os.path.getmtime(full_path))
                    if latest_pdf is None or p_date > latest_pdf: latest_pdf = p_date
                except OSError: pass
        
        if dcm_date is None: return False, False, None, None
        pdf_ok = bool(latest_pdf and latest_pdf.date() >= dcm_date.date())
        return True, pdf_ok, (latest_pdf if pdf_ok else None), energy
    except Exception: return False, False, None, None

def read_tomo_scandidos(folder_path: Optional[str]):
    if not folder_path: return None, None
    try:
        for root, file_name in iter_files(folder_path):
            name, ext = os.path.splitext(file_name)
            if ext.lower() in [".dcm", ".pdf", ".xml", ".json"]: continue
            if (name.count(".") >= 5 and name.replace(".", "").isdigit()) or SCANDIDOS_TOMO_SIGNATURE in name:
                with open(os.path.join(root, file_name), "rb") as f:
                    tokens = re.findall(r"[A-Za-z0-9_.\-\/\^]+", f.read().decode("latin-1", errors="ignore").replace("\x00", " "))
                    fraction, duration = None, None
                    for i, t in enumerate(tokens):
                        if "_QA" in t:
                            parts, j = [], i
                            while j > 0 and tokens[j - 1] != "0":
                                parts.insert(0, tokens[j].replace("_QA", "").replace("_OK", "").strip())
                                j -= 1
                            if j > 0: parts.insert(0, tokens[j].replace("_QA", "").replace("_OK", "").strip())
                            fraction = " ".join(parts)
                        if t == "VERIFICATION":
                            for j in range(i, min(i + 20, len(tokens))):
                                if re.match(r"\d+\.\d{4,}", tokens[j]):
                                    duration = float(tokens[j])
                                    break
                    return fraction, duration
    except Exception: pass
    return None, None

class NetworkScannerThread(QThread):
    # Emet : tab_attribute, row_index, updated_dict
    row_updated = Signal(str, int, dict)
    scan_finished = Signal(list)

    def __init__(self, data: DashboardData, parent=None):
        super().__init__(parent)
        self.data = data
        self.is_cancelled = False

    def cancel(self):
        self.is_cancelled = True

    def run(self):
        errors = []
        path_tomo2 = r"\\nasdata1\TOMO\02 - CQ Patients\0_DELTA4\TOMO2"
        path_tomo4 = r"\\nasdata1\TOMO\02 - CQ Patients\0_DELTA4\TOMO4"
        path_tomo7 = r"\\nasdata1\TOMO\02 - CQ Patients\0_DELTA4\RADI7"
        path_nova = {
            "EC": r"\\srv015\Radiophysique_acquisition\Patients_stereo_EC_IUC",
            "Hyperarc": r"\\srv015\Radiophysique_acquisition\Patients_stereo_Hyperarc",
            "RA": r"\\srv015\Radiophysique_acquisition\Patients_RA",
        }
        
        def build_cache(p, sub=None):
            t = os.path.join(p, sub) if sub else p
            c = {}
            try:
                with os.scandir(t) as entries:
                    for e in entries:
                        if e.is_dir(): c[e.name] = e.path
            except Exception as exc: errors.append(f"{t} -> {exc}")
            return c

        def find(cache, ipp):
            if not ipp: return None, None
            ipp = str(ipp).strip()
            for n, p in cache.items():
                if ipp in n: return n, p
            return None, None

        c_tomo2 = build_cache(path_tomo2)
        c_tomo4 = build_cache(path_tomo4)
        c_tomo7 = build_cache(path_tomo7)
        yr = str(datetime.now().year)
        c_ec = build_cache(path_nova["EC"], yr)
        c_hyper = build_cache(path_nova["Hyperarc"], yr)
        c_ra = build_cache(path_nova["RA"], yr)

        if self.is_cancelled: return

        # TOMO
        for tab_id, rows, cache in [("tomo2", self.data.tomo2, c_tomo2), ("tomo4", self.data.tomo4, c_tomo4), ("tomo7", self.data.tomo7, c_tomo7)]:
            for i, r in enumerate(rows):
                if self.is_cancelled: return
                n, p = find(cache, r.get("ipp"))
                d_ok, p_ok, p_date, _ = check_dicom_and_pdf(p)
                frac, dur = read_tomo_scandidos(p)
                r.update({"folder_path": p, "existing_folder": n is not None, "existing_dicom": d_ok, "existing_pdf": p_ok, "pdf_date": p_date, "energy": TOMO_DEFAULT_ENERGY, "tomo_duration_min": dur, "tomo_fraction": frac})
                self.row_updated.emit(tab_id, i, r)

        # NOVA
        sources = [("EC", c_ec), ("Hyperarc", c_hyper), ("RA", c_ra)]
        for i, r in enumerate(self.data.nova):
            if self.is_cancelled: return
            n, p, m = None, None, None
            for lbl, c in sources:
                n, p = find(c, r.get("ipp"))
                if n: m = lbl; break
            d_ok, p_ok, p_date, en = check_dicom_and_pdf(p, True)
            r.update({"folder_path": p, "existing_folder": n is not None, "existing_dicom": d_ok, "existing_pdf": p_ok, "pdf_date": p_date, "energy": en, "nova_machine": m})
            self.row_updated.emit("nova", i, r)

        if not self.is_cancelled:
            self.scan_finished.emit(errors)