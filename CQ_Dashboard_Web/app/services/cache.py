# -*- coding: utf-8 -*-
"""
CQ Dashboard — Pré-calcul en arrière-plan (cache serveur).

Un thread démon recalcule périodiquement build_dashboard_data() pour TOUS les
onglets machines + get_db_last_update_time(), et garde le résultat en mémoire.
Les routes lisent simplement ce cache : l'affichage est instantané, même si
aucun navigateur n'est connecté, et le clic sur un onglet machine ne déclenche
aucun calcul lourd.

Intervalle par défaut : 120 s (2 min), car la base SQL ARIA (miroir MySQL) est
synchronisée toutes les 3 minutes. Réglable dans l'Admin via le paramètre
CACHE_REFRESH_SECONDS (entre 10 s et 1 h).

Si MySQL ou le NAS est indisponible, les dernières données connues sont
conservées et exposées avec l'âge du cache (cache_age_sec) au lieu d'afficher
une page vide.
"""
import threading
import time
from datetime import datetime

DEFAULT_CACHE_REFRESH_SECONDS = 120  # 2 minutes (base ARIA synchronisée toutes les 3 min)

_lock = threading.Lock()
_cache = {}                  # tab_label -> {"data": dict, "ts": float}
_db_last_update = None       # dict renvoyé par business.get_db_last_update_time()
_last_refresh_at = None      # datetime du dernier rafraîchissement complet/partiel
_last_refresh_error = None   # str | None
_refreshing = False
_thread_started = False
_last_attempt = {}           # tab_label -> time.time() du dernier essai (succès ou échec)


def get_refresh_interval_sec() -> int:
    """Intervalle de rafraîchissement du cache (secondes), lu depuis l'Admin."""
    try:
        from app.models_local import Setting
        raw = Setting.get_val("CACHE_REFRESH_SECONDS", "")
        val = int(str(raw).strip())
        if 10 <= val <= 3600:
            return val
    except Exception:
        pass
    return DEFAULT_CACHE_REFRESH_SECONDS


def refresh_all(app=None, tab_label=None) -> bool:
    """
    Recalcule le cache. Si `tab_label` est fourni, seul cet onglet est recalculé.
    Ne fait rien si un rafraîchissement est déjà en cours. Retourne True si exécuté.
    """
    global _refreshing, _last_refresh_at, _last_refresh_error, _db_last_update

    with _lock:
        if _refreshing:
            return False
        _refreshing = True

    try:
        if app is None:
            from flask import current_app
            app = current_app._get_current_object()

        with app.app_context():
            from app.models_local import CQTab
            from app.services import business

            query = CQTab.query.order_by(CQTab.display_order)
            tabs = query.all()
            if tab_label:
                tabs = [t for t in tabs if t.label == tab_label]

            errors = []
            for t in tabs:
                try:
                    data = business.build_dashboard_data(t.label)
                    with _lock:
                        _cache[t.label] = {"data": data, "ts": time.time()}
                except Exception as e:
                    errors.append(f"{t.label}: {e}")
                    print(f"[Cache] Erreur calcul onglet {t.label}: {e}")

            try:
                db_info = business.get_db_last_update_time()
                with _lock:
                    _db_last_update = db_info
            except Exception as e:
                errors.append(f"db_last_update: {e}")
                print(f"[Cache] Erreur horodatage SQL ARIA: {e}")

            with _lock:
                _last_refresh_at = datetime.now()
                _last_refresh_error = "; ".join(errors) if errors else None
        return True
    finally:
        with _lock:
            _refreshing = False


def get_snapshot(tab_label) -> dict:
    """Copie thread-safe de l'état courant pour un onglet."""
    with _lock:
        entry = _cache.get(tab_label) or {}
        data = entry.get("data")
        ts = entry.get("ts")
        age = (time.time() - ts) if ts else None
        refreshed = _last_refresh_at.strftime("%H:%M:%S") if _last_refresh_at else None
        return {
            "data": data,
            "cache_age_sec": round(age, 1) if age is not None else None,
            "cache_refreshed_at": refreshed,
            "cache_error": _last_refresh_error,
            "db_last_update": _db_last_update,
        }


def get_data(tab_label, force: bool = False) -> dict:
    """
    Point d'entrée pour /api/data :
    - lecture instantanée du cache (comportement normal) ;
    - calcul synchrone à la demande si le cache est vide ou si force=True.
    """
    if force:
        refresh_all(tab_label=tab_label)
        return get_snapshot(tab_label)

    snap = get_snapshot(tab_label)
    if snap["data"] is None:
        # Cache pas encore rempli (tout premier accès après le démarrage).
        # On tente un calcul, mais sans le refaire à chaque requête si MySQL/NAS
        # est indisponible : on attend au moins un cycle de rafraîchissement.
        with _lock:
            last = _last_attempt.get(tab_label, 0)
        if time.time() - last >= min(30, get_refresh_interval_sec()):
            with _lock:
                _last_attempt[tab_label] = time.time()
            refresh_all(tab_label=tab_label)
            snap = get_snapshot(tab_label)
    return snap


def _loop(app):
    # Laisse l'application finir de démarrer avant le premier calcul
    time.sleep(2)
    while True:
        try:
            refresh_all(app=app)
        except Exception as e:
            print(f"[Cache] Erreur rafraîchissement global: {e}")
        time.sleep(get_refresh_interval_sec())


def start_background_refresh(app):
    """Démarre le thread de pré-calcul (une seule fois)."""
    global _thread_started
    with _lock:
        if _thread_started:
            return
        _thread_started = True
    t = threading.Thread(target=_loop, args=(app,), daemon=True, name="cq-cache-refresh")
    t.start()
    print("[Cache] Pré-calcul en arrière-plan démarré (INTERVALLE : CACHE_REFRESH_SECONDS, défaut 120 s)")
