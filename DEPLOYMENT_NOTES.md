# Deploiement Yorwani

Etat verifie le 17 octobre 2026. Cette configuration est preparee ; aucun compte Neon, serveur ou domaine n'a ete cree.

## Etat local

- La boutique Django tourne localement avec SQLite et des donnees existantes.
- Les migrations 0009, 0010 et 0011 sont appliquees. Une sauvegarde SQLite a ete creee avant chacune des migrations pertinentes dans `.private_backups/`.
- `DATABASE_URL` n'est pas configure dans le `.env` local ; aucun projet Neon n'est rattache.
- L'agent Jumia est en simulation, desactive par defaut. Il ne se connecte pas, ne choisit pas de variante, ne met rien au panier et ne paie rien.
- Aucun deploiement distant ni achat n'a ete effectue.

## PostgreSQL / Neon

1. Creer un projet dans la console Neon et garder son URL de connexion dans le `.env` prive ou dans le gestionnaire de secrets du serveur.
2. Copier l'URL pooler fournie par Neon dans `DATABASE_URL`; garder `DB_SSL_REQUIRE=1`.
3. Verifier que la cible est une base vide. `migrate_sqlite_to_postgres` refuse une cible avec des donnees applicatives, sauvegarde SQLite, transfere les modeles de la boutique et les groupes d'acces, puis compare les comptes et empreintes des donnees.

```powershell
.\.venv\Scripts\Activate.ps1
python manage.py check_database
python manage.py migrate
python manage.py migrate_sqlite_to_postgres --confirm-target-empty
```

La source SQLite reste intacte. Les fichiers de `media/` sont hors base et doivent etre copies separement. Verifier produits, commandes, comptes, images et totaux avant le basculement. Conserver les sauvegardes.

La page officielle Neon consult ee le 17 octobre 2026 annonce Free avec 100 CU-heures et 1 Go par projet, avec mise en veille apres cinq minutes d'inactivite. Launch annonce un cout typique de 15 USD/mois. Free ne garantit pas un service continu. [Tarifs Neon](https://neon.com/pricing).

## Agent Jumia en simulation

Le worker inspecte une fiche publique Jumia et verifie le titre, la variante, la disponibilite, la devise et le plafond de prix fournisseur. Donnee manquante, CAPTCHA, connexion requise ou ecart : il s'arrete pour revue humaine. Il ne passe jamais commande.

1. Renseigner dans la fiche produit un lien HTTPS Jumia public et un prix maximum fournisseur en FCFA.
2. Seules les commandes payees et confirmees peuvent etre mises en file.
3. Faire d'abord un apercu sans ecriture :

```powershell
python manage.py queue_jumia_orders --order-ref YOR-REFERENCE --dry-run
python manage.py queue_jumia_orders --order-ref YOR-REFERENCE
```

Le worker utilise les verrous PostgreSQL et reste desactive jusqu'a configuration explicite. Garder son mode a `simulation`.

```powershell
python -m playwright install chromium
$env:JUMIA_AGENT_ENABLED = "1"
$env:JUMIA_AGENT_MODE = "simulation"
python manage.py run_jumia_agent --once
```

Une tache en revue humaine ne peut etre relancee qu'apres correction et validation explicite : `python manage.py requeue_jumia_task ID --human-reviewed`.

Un endpoint Llama compatible OpenAI est facultatif. Sans cle, l'analyse locale est utilisee. Si configure, seuls les attributs du produit fournisseur sont transmis, jamais les donnees client.

## Docker

La configuration comprend Django/Gunicorn, Caddy pour HTTPS, medias persistants et un worker optionnel. PostgreSQL doit rester configure sur Neon.

1. Creer un VPS, regler les DNS et copier le projet.
2. Creer `.env` depuis `.env.example`; utiliser une cle Django aleatoire, `DJANGO_DEBUG=0`, des hotes autorises et des origines CSRF HTTPS exactes.
3. Configurer Neon, migrer les donnees et copier `media/`.
4. Valider puis demarrer :

```sh
docker compose config
docker compose up -d --build web caddy
docker compose ps
docker compose logs --tail=100 web caddy
```

Le worker optionnel se lance avec `docker compose --profile agent up -d --build worker`. Le pare-feu devrait limiter les ports entrants a SSH, HTTP et HTTPS.

La grille Hetzner applicable depuis le 15 juin 2026 annonce CX23 a 5,49 EUR/mois hors IPv4 et TVA pour Allemagne/Finlande. Avec Neon Launch a 15 USD typiques, le total peut atteindre ou depasser 10 000 a 15 000 FCFA apres change, taxes, IPv4, domaine et sauvegardes. [Tarifs Hetzner](https://docs.hetzner.com/general/infrastructure-and-availability/price-adjustment/).

## Sauvegardes

`scripts/postgres_backup.py` cree une archive custom dans `.private_backups/`, sans mettre le mot de passe dans les arguments ou journaux. Il faut `pg_dump` compatible. Planifier une copie chiffree quotidienne hors serveur et sauvegarder aussi les medias.

La restauration est destructive et exige `--confirm-overwrite`. Tester d'abord sur une base vide distincte, avec une archive verifiee. Ne jamais lancer une restauration en production sans avoir verifie la cible et l'archive.

## A faire avant mise en production

- Creer/choisir Neon, domaine et VPS, puis configurer secrets prives.
- Transferer `media/` et valider les donnees migrees.
- Saisir plafonds fournisseurs et confirmer la devise attendue.
- Choisir si les metadonnees produit peuvent etre envoyees a l'endpoint Llama.
- Installer Docker sur la machine de deploiement et valider la configuration Compose.
- Protege le `.env` par ACL NTFS sous Windows ou droits 600 sous Linux. Ne jamais saisir mot de passe Jumia ni de paiement.
