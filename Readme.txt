Accès BD à distance après avoir fait un git clone

1 - Creation d'une session() sur la BD via la declaration des variables d'environements dans .env :
	Ce fichier est present dans le gitignore. Le creer si absent apres clone git
	Exemple de modele de .env
	# .env
	DATABASE_USER=***
	DATABASE_PASSWORD=****
	DATABASE_HOST=****
	DATABASE_NAME=****
	DATABASE_URL=mysql+pymysql://****:******@*****:*****/****

2 - Création d'un virtual environment (sous visual studio par exemple)

3 - dans mon venv il faut avoir pip install sqlacodegen, pip install pymysql, pip install sqlalchemy, pip install python-dotenv. (sqlacodegen Permet à partir de la connexion à distance à la base de générer un models.py automatiquement)

3 - Il faut malgré tout déclarer les tables et attributs de tables de la base pour pouvoir faire des requetes simples 
	=> Il faut générer (ou régénerer si MAJ des talbes de la BD) models.py :
	- Modifier comme un fichier texte le update_models.ps1 pour a la ligne 	
	# Exécuter sqlacodegen avec python -m pour générer models.py
	NOM_DU_VENV\Scripts\python.exe -m sqlacodegen $env:DATABASE_URL --outfile models.py

	- Dans une console powershell faire cd vers le dossier du projet

	- Dans son venv (cd dossier venv\script puis .\activate) : 
		.\update_models.ps1
		=>Ce fichier va generer models.py puis le modifier un peu. On a donc après avoir lancé le script update_models models.py dans le dossier du porojet.
		Ce script permet que :
		- que les classes heritent de base.py et non de declarative_base. 
		- que models_extended.py herite des classes de models.py + classes de utils_mixin.py (Utils_mixin.py permet de declarer des fonctions tres utiles comme .to_dict() qui s'appliquent à toutes les tables de la base)
	
Au final dans mon main j'utilise donc les tables qui ont été modifées dans mon models_extended.py : from models_extended import * et non from models import *



GH = MAJ MODEL
(env_piloteRT) PS C:\Users\hangard\Documents\PiloteRT> .\update_models.ps1
DATABASE_USER     = arnaud
DATABASE_PASSWORD = Allezco81!
DATABASE_HOST     = ECLIPSE30
DATABASE_NAME     = db_iuct
models.py modifiÃ© avec succÃ¨s.
(env_piloteRT) PS C:\Users\hangard\Documents\PiloteRT> ^C
(env_piloteRT) PS C:\Users\hangard\Documents\PiloteRT>


