# Yorwani

Boutique en ligne Django hébergée sur Render. PostgreSQL et le stockage des images de production sont fournis par Supabase. Neon n'est pas utilisé.

## Architecture

Consulter [la documentation d'architecture](docs/architecture.md) pour les modèles, relations, permissions, commandes existantes, variables attendues et limites de vérification de la production.

Le dépôt utilise actuellement `requirements.txt` et `pip`. `uv` n'est pas encore configuré (`pyproject.toml` et `uv.lock` absents).

## Développement local

Le projet exige un `DATABASE_URL` PostgreSQL. Avant de lancer Django, configurer un fichier `.env` local privé avec une base de développement Supabase (ou un environnement de test isolé) et les autres variables nécessaires. Ne jamais utiliser des identifiants de production dans un fichier partagé ou committé.

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
python manage.py runserver
```

Créer un superutilisateur dans une base de développement si nécessaire :

```powershell
python manage.py createsuperuser
```

N'exécuter `migrate` que sur une base de développement choisie explicitement. Ne pas lancer `seed_store` sur la production : cette commande ajoute des données de démonstration.

## Administration

Les visiteurs consultent le catalogue et commandent avec un compte. Le tableau propriétaire se trouve sous `/maison/` et l'admin Django sous `/admin/`; les deux sont réservés aux superutilisateurs.

## Import du catalogue

L'import CSV en aperçu et application contrôlée est décrit dans [docs/catalog-import.md](docs/catalog-import.md). Les produits importés restent en brouillon.

## Déploiement

Render construit le service avec `pip install -r requirements.txt`, collecte les fichiers statiques et démarre Django avec Gunicorn. Le `startCommand` actuel lance aussi les migrations Django. Toute mise à jour du code qui déclenche un déploiement peut donc exécuter les migrations présentes; examiner ce comportement avant de déployer.

La configuration de production des variables secrètes est gérée dans Render. Le dépôt ne donne pas accès à leurs valeurs. Ne jamais committer `.env`, les clés S3 ou la chaîne `DATABASE_URL`.
