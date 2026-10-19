from datetime import datetime, timedelta

LOOKBACK_DAYS = 14
MAX_WALK_DEPTH = 3
SCANDIDOS_TOMO_SIGNATURE = '114358'
TOMO_DEFAULT_ENERGY = '6FFF'
CQ_PHYSIQUE_PATIENT_ID = 3
PATIENT_ID_EXCLUDED_FROM_REMAINING = 4


def day_bounds(now=None):
    now = now or datetime.now()
    start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    end = start + timedelta(days=1)
    return start, end


def normalize_device(device):
    if not device:
        return ''
    return str(device).upper().replace(' ', '').strip()


def machine_matches_task(machine_name, machine_label, device_ids, task_focus):
    """Associe une tache ARIA a une machine locale configurable."""
    focus = str(task_focus or '').lower()
    candidates = []
    if machine_label:
        candidates.append(str(machine_label).lower())
    if machine_name:
        candidates.append(str(machine_name).lower())
        candidates.append(str(machine_name).lower().replace(' ', ''))
    if device_ids:
        for part in str(device_ids).split(','):
            p = part.strip().lower()
            if p:
                candidates.append(p)
                candidates.append(p.replace(' ', ''))

    for c in candidates:
        if c and c in focus:
            return True

    # Cas Nova generiques
    name_u = str(machine_name or '').upper()
    label_u = str(machine_label or '').upper()
    if 'NOVA' in name_u or 'NOVA' in label_u:
        if 'ruby' in focus or 'octa' in focus or 'nova' in focus:
            return True
    return False


def sort_by_met_start(rows):
    return sorted(rows, key=lambda r: r.get('met_start') or datetime.max)


def clean_cq_rows(rows):
    """Dedoublonnage leger par patient + focus."""
    seen = set()
    out = []
    for r in rows:
        key = (r.get('patient_id'), str(r.get('task_display_focus') or '').lower())
        if key in seen:
            continue
        seen.add(key)
        out.append(r)
    return out


def serialize_dt(value):
    if not value:
        return None
    if isinstance(value, datetime):
        return value.strftime('%d/%m/%Y %H:%M')
    return str(value)