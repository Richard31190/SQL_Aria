import sys
import os

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from app import create_app

app = create_app()

if __name__ == '__main__':
    print("Démarrage du serveur CQ Dashboard sur http://localhost:5000")
    app.run(host='0.0.0.0', port=5000, debug=True)