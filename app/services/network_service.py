import os
import re
from datetime import datetime
from typing import Optional, Dict

try:
    import pydicom
except ImportError:
    pydicom = None

from .config_utils import MAX_WALK_DEPTH, SCANDIDOS_TOMO_SIGNATURE, TOMO_DEFAULT_ENERGY


def iter_files(root_path: str, max_depth: int = MAX_WALK_DEPTH):
    root_path = os.path.abspath(root_path)
    for current, dirs, files in os.walk(root_path):
        rel = current[len(root_path):]
        if rel.count(os.sep) >= max_depth:
            dirs.clear()
        for file_name in files:
            yield current, file_name


def check_dicom_and_pdf(folder_path: Optional[str], read_energy: bool = False):
    if not folder_path:
        return False, False, None, None

    dcm_date, energy, latest_pdf = None, None, None
    try:
        for root, file_name in iter_files(folder_path):
            full_path = os.path.join(root, file_name)
            lower = file_name.lower()

            if lower.endswith('.dcm'):
                if dcm_date is None:
                    try:
                        dcm_date = datetime.fromtimestamp(os.path.getmtime(full_path))
                    except OSError:
                        pass

                if read_energy and energy is None and 'RP' in file_name.upper() and pydicom is not None:
                    try:
                        ds = pydicom.dcmread(full_path, stop_before_pixels=True)
                        b = ds.BeamSequence[0]
                        e_val = str(b.ControlPointSequence[0].NominalBeamEnergy)
                        fluence = str(getattr(b.PrimaryFluenceModeSequence[0], 'FluenceModeID', ''))
                        m = 'FFF' if 'FFF' in fluence else 'X'
                        energy = f'{e_val}{m}'
                    except Exception:
                        pass

            elif lower.endswith('.pdf'):
                try:
                    p_date = datetime.fromtimestamp(os.path.getmtime(full_path))
                    if latest_pdf is None or p_date > latest_pdf:
                        latest_pdf = p_date
                except OSError:
                    pass

        if dcm_date is None:
            return False, False, None, None
        pdf_ok = bool(latest_pdf and latest_pdf.date() >= dcm_date.date())
        return True, pdf_ok, (latest_pdf if pdf_ok else None), energy
    except Exception:
        return False, False, None, None


def read_tomo_scandidos(folder_path: Optional[str]):
    if not folder_path:
        return None, None
    try:
        for root, file_name in iter_files(folder_path):
            name, ext = os.path.splitext(file_name)
            if ext.lower() in ['.dcm', '.pdf', '.xml', '.json']:
                continue
            if (name.count('.') >= 5 and name.replace('.', '').isdigit()) or SCANDIDOS_TOMO_SIGNATURE in name:
                with open(os.path.join(root, file_name), 'rb') as f:
                    raw = f.read().decode('latin-1', errors='ignore').replace('\x00', ' ')
                    tokens = re.findall(r'[A-Za-z0-9_.\-\/\^]+', raw)
                    fraction, duration = None, None
                    for i, t in enumerate(tokens):
                        if '_QA' in t:
                            parts, j = [], i
                            while j > 0 and tokens[j - 1] != '0':
                                parts.insert(0, tokens[j].replace('_QA', '').replace('_OK', '').strip())
                                j -= 1
                            if j > 0:
                                parts.insert(0, tokens[j].replace('_QA', '').replace('_OK', '').strip())
                            fraction = ' '.join(parts)
                        if t == 'VERIFICATION':
                            for j in range(i, min(i + 20, len(tokens))):
                                if re.match(r'\d+\.\d{4,}', tokens[j]):
                                    duration = float(tokens[j])
                                    break
                    return fraction, duration
    except Exception:
        pass
    return None, None


def build_folder_cache(path: str, sub: Optional[str] = None) -> Dict[str, str]:
    target = os.path.join(path, sub) if sub else path
    cache = {}
    try:
        with os.scandir(target) as entries:
            for e in entries:
                if e.is_dir():
                    cache[e.name] = e.path
    except Exception:
        pass
    return cache


def find_patient_folder(cache: Dict[str, str], ipp: Optional[str]):
    if not ipp:
        return None, None
    ipp = str(ipp).strip()
    for name, path in cache.items():
        if ipp in name:
            return name, path
    return None, None


def enrich_rows_with_nas(rows, dicom_path: str, is_tomo: bool = True):
    """Enrichit les lignes CQ avec presence dossier/DICOM/PDF sur le NAS."""
    if not dicom_path:
        for r in rows:
            r.update({
                'folder_path': None,
                'existing_folder': False,
                'existing_dicom': False,
                'existing_pdf': False,
                'pdf_date': None,
                'energy': TOMO_DEFAULT_ENERGY if is_tomo else None,
                'tomo_duration_min': None,
                'tomo_fraction': None,
            })
        return rows

    # Nova : sous-dossier annee courante souvent present
    caches = [build_folder_cache(dicom_path)]
    year = str(datetime.now().year)
    year_cache = build_folder_cache(dicom_path, year)
    if year_cache:
        caches.append(year_cache)

    for r in rows:
        folder_name, folder_path = None, None
        for cache in caches:
            folder_name, folder_path = find_patient_folder(cache, r.get('ipp'))
            if folder_name:
                break

        d_ok, p_ok, p_date, energy = check_dicom_and_pdf(folder_path, read_energy=not is_tomo)
        frac, dur = (None, None)
        if is_tomo:
            frac, dur = read_tomo_scandidos(folder_path)
            energy = energy or TOMO_DEFAULT_ENERGY

        r.update({
            'folder_path': folder_path,
            'existing_folder': folder_name is not None,
            'existing_dicom': d_ok,
            'existing_pdf': p_ok,
            'pdf_date': p_date,
            'energy': energy,
            'tomo_duration_min': dur,
            'tomo_fraction': frac,
        })
    return rows