# *** run.py ***
import sys
import os
import signal

# Securite pour le path d'execution sous Windows
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from app import create_app

app = create_app()


def handle_sigint(sig, frame):
    """Force l'arret propre sous Windows avec Waitress"""
    print("\n= Arret du serveur...")
    os._exit(0)


if __name__ == '__main__':
    # Intercepte Ctrl+C meme si Waitress le bloque
    signal.signal(signal.SIGINT, handle_sigint)

    print("= Starting CQ Dashboard Web Application...")
    print("= Access the dashboard at http://localhost:5000")
    print("= Press Ctrl+C to stop the server.")

    try:
        from waitress import serve
        serve(app, host='0.0.0.0', port=5000)
    except ImportError:
        print("Waitress not found, fallback to Flask dev server...")
        app.run(host='0.0.0.0', port=5000, debug=True)
    except KeyboardInterrupt:
        print("\n= Arret du serveur...")
        sys.exit(0)