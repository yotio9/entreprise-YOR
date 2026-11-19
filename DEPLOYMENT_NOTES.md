# Notes de déploiement Yorwani

## Architecture retenue

- Application Django : Render.
- PostgreSQL : Supabase.
- Images et médias de production : Supabase Storage via S3.
- Neon n'est pas utilisé; aucune migration depuis ou vers Neon n'est prévue.

Le Blueprint versionné est `render.yaml`. Le service s'appelle `yorwani`; il installe les dépendances depuis `requirements.txt`, collecte les fichiers statiques et sert Django par Gunicorn. Son `startCommand` exécute `python manage.py migrate --noinput` avant le serveur.

## Variables attendues

Les noms et le rôle des variables sont dans `render.yaml` et `yorwani/settings.py` :

- `DATABASE_URL` : URL PostgreSQL Supabase (Render `sync:false`).
- `DB_SSL_REQUIRE` : SSL PostgreSQL, activé par défaut.
- `DJANGO_SECRET_KEY`, `DJANGO_DEBUG` et les variables Django associées.
- `NEXT_PUBLIC_SUPABASE_URL`, `SUPABASE_STORAGE_BUCKET`, `SUPABASE_STORAGE_REGION`.
- `SUPABASE_S3_ACCESS_KEY_ID` et `SUPABASE_S3_SECRET_ACCESS_KEY` (secrets Render `sync:false`).

Ne jamais copier une valeur secrète dans Git, une issue, un prompt, un journal ou un fichier documentaire. Vérifier les domaines et la présence des variables sans divulguer leurs valeurs.

## Vérification de production

Le contenu de `render.yaml` décrit les paramètres déclaratifs, pas les valeurs saisies dans le tableau Render. Vérifier dans Render que `DATABASE_URL` cible le projet Supabase attendu et que les identifiants S3 appartiennent au même projet. Ne pas coller les secrets dans une conversation.

Au 17 novembre 2026, le tableau Render n'était pas accessible à l'agent lors de l'inspection. La vérification de production reste donc à faire. L'inspection locale, sans divulguer les valeurs, a trouvé un `DATABASE_URL` présent mais non analysable et aucune variable locale de bucket, région ou clés S3; le fichier local n'a pas été modifié.

## Déploiements et migrations

Comme les migrations sont dans la commande de démarrage Render, elles s'exécutent lors du démarrage du service. Avant un déploiement, lire les migrations en attente et déterminer leur impact sur les données. Ne jamais exécuter une migration, une restauration ou un outil de transfert sur la production sans demande explicite et plan vérifié.

Les scripts `scripts/postgres_backup.py` et `scripts/postgres_restore.py` sont disponibles pour PostgreSQL. La restauration utilise `pg_restore --clean --if-exists` et peut écraser les données; la réserver à un environnement choisi et explicitement confirmé.

## Dépendances

Le dépôt déclare les dépendances dans `requirements.txt`; `render.yaml` et `Dockerfile` utilisent pip. `uv` ne sera configuré qu'après ajout planifié de `pyproject.toml` et `uv.lock` et adaptation du déploiement. Cette documentation ne change pas le gestionnaire de dépendances.
