# ==============================================================================
# COMMANDES DE COMPILATION PYINSTALLER (PowerShell) :
#
# 1. Nettoyage des anciens builds :
# Remove-Item -Recurse -Force build, dist, *.spec -ErrorAction SilentlyContinue
#
# 2. Compilation sur une seule ligne :
# pyinstaller --noconfirm --clean --onedir --console --name "CQ_Dashboard" --hidden-import=flask_sqlalchemy --hidden-import=sqlalchemy --hidden-import=sqlalchemy.dialects.sqlite --hidden-import=mysql.connector --hidden-import=pydicom --hidden-import=waitress --hidden-import=dotenv --collect-all flask_sqlalchemy --collect-all sqlalchemy --collect-all pydicom --collect-all waitress --add-data "app/templates;app/templates" --add-data "app/static;app/static" --add-data "instance;instance" run.py
# ==============================================================================

import sys
import os
import signal
import socket
import webbrowser
from threading import Timer

# Securite pour le path d'execution sous Windows
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from app import create_app

app = create_app()


def handle_sigint(sig, frame):
    """Force l'arret propre sous Windows avec Waitress lors d'un Ctrl+C"""
    print("\n=====================================================")
    print("  Arret du serveur CQ Dashboard...")
    print("=====================================================")
    os._exit(0)


def get_local_ip():
    """Recupere l'adresse IP locale de la machine sur le reseau."""
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return "127.0.0.1"


def open_browser(url):
    """Ouvre automatiquement le navigateur web par defaut."""
    try:
        webbrowser.open(url)
    except Exception:
        pass


if __name__ == '__main__':
    # Intercepte Ctrl+C meme si Waitress le bloque
    signal.signal(signal.SIGINT, handle_sigint)

    PORT = 5000
    local_ip = get_local_ip()
    url_local = f"http://localhost:{PORT}"
    url_reseau = f"http://{local_ip}:{PORT}"

    print("\n=====================================================")
    print("  CQ Dashboard - Serveur demarre avec succes !")
    print(f"  Acces local  : {url_local}")
    print(f"  Acces reseau : {url_reseau}")
    print("  Appuyez sur Ctrl+C pour arreter le serveur.")
    print("=====================================================\n")

    # Ouvre automatiquement le navigateur local apres 1.5 seconde
    Timer(1.5, open_browser, args=[url_local]).start()

    try:
        from waitress import serve
        serve(app, host='0.0.0.0', port=PORT, threads=6)
    except ImportError:
        print("Waitress non trouve, bascule sur le serveur Flask dev...")
        app.run(host='0.0.0.0', port=PORT, debug=True)
    except KeyboardInterrupt:
        print("\n= Arret du serveur...")
        sys.exit(0)