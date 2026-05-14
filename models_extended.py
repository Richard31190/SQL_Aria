# models_extended.py
from models import *  # Importer toutes les classes de models.py
from utils_mixin import ToDictMixin  # Importer le mixin

# Faire une copie de globals() pour eviter l'erreur
globals_copy = dict(globals())

# Pour chaque classe de models.py
for name, cls in globals_copy.items():
    if isinstance(cls, type):  # Verifier que c'est une classe
        if hasattr(cls, '__table__'):  # Verifier que la classe est une table mappee
            # Ajouter dynamiquement le mixin ToDictMixin
            cls.__bases__ = (ToDictMixin,) + cls.__bases__
