# Architecture actuelle de Yorwani

État du dépôt inspecté le 17 novembre 2026. Ce document décrit le code versionné; il ne prouve pas que les valeurs privées configurées dans Render sont valides.

## Vue d'ensemble

```text
Navigateur → Render (Django / Gunicorn)
                    ├── Supabase PostgreSQL (DATABASE_URL)
                    └── Supabase Storage via S3 (médias)
```

Le fournisseur PostgreSQL retenu par le propriétaire est Supabase. Neon n'est pas utilisé et aucune migration de base n'est prévue. Render héberge l'application Django.

## Application et dépendances

- Projet Django : `yorwani/`; application métier : `boutique/`.
- La version des dépendances déclarée est Django `>=5.2,<5.3`; les dépendances sont listées dans `requirements.txt`.
- Le dépôt ne contient pas `pyproject.toml` ni `uv.lock`. Le Blueprint Render et le Dockerfile installent les dépendances avec pip. uv n'est donc pas encore la configuration effective du dépôt.
- Les routes sont dans `boutique/urls.py`; les vues métier sont principalement dans `boutique/views.py`.

## Base de données

`yorwani/settings.py` exige `DATABASE_URL`, puis utilise `dj-database-url` pour configurer PostgreSQL. SQLite est désactivé par ce réglage. Le SSL est exigé par défaut via `DB_SSL_REQUIRE=1`.

Dans `render.yaml`, `DATABASE_URL` a `sync:false`. Le dépôt ne contient donc pas l'hôte réel ni ne permet de vérifier que la valeur dans le tableau Render correspond à Supabase.

Le `startCommand` Render exécute `python manage.py migrate --noinput` avant Gunicorn à chaque démarrage/déploiement. Aucune migration ni aucun déploiement n'a été déclenché pour ce travail documentaire. Toute future migration devra être examinée séparément avant déploiement.

## Stockage des médias

`yorwani/settings.py` configure `FileSystemStorage` par défaut en développement local. En production (`DEBUG=0`), il exige les variables Supabase et configure `django-storages` (`storages.backends.s3.S3Storage`) avec un endpoint S3 Supabase et des URL publiques sans signature.

Le Blueprint déclare `NEXT_PUBLIC_SUPABASE_URL`, `SUPABASE_STORAGE_BUCKET`, `SUPABASE_STORAGE_REGION`, `SUPABASE_S3_ACCESS_KEY_ID` et `SUPABASE_S3_SECRET_ACCESS_KEY`. Les deux clés sont `sync:false`. Le Blueprint versionné indique le bucket `Yorwani-media` et la région `eu-west-3`. Cela confirme l'intention de configurer Supabase Storage, mais pas que les variables secrètes sont correctement renseignées dans Render.

Les clés S3 doivent rester côté serveur. Ne pas les exposer au navigateur ni au dépôt. Les champs d'image sont `Product.image`, `ProductImage.image` et `Category.image`; certains modèles autorisent aussi des URL externes. Utiliser le stockage Django configuré plutôt qu'un accès S3 codé en dur.

## Modèles et relations

Définis dans `boutique/models.py` :

| Modèle | Rôle et relations |
|---|---|
| `User` | Modèle utilisateur Django personnalisé pour les comptes clients et administrateurs. |
| `Category` | Collection/catégorie avec image et état actif; `Product.category` la référence et empêche sa suppression lorsqu'elle est utilisée. |
| `Product` | Produit avec catégorie, prix, tailles, stock, état actif/mis en avant, image principale et champs d'image externe. |
| `ProductImage` | Galerie liée à `Product`, avec image, URL éventuelle, texte alternatif et position. |
| `Order` | Commande liée à `User`; informations de livraison, montants, statuts commande/paiement, expédition et coupon éventuel. |
| `OrderItem` | Ligne liée à une commande et éventuellement à un produit; garde des instantanés du nom et du prix, la taille, les personnalisations et la quantité. |
| `Favorite` | Association utilisateur-produit avec unicité de la paire. |
| `Review` | Avis utilisateur-produit avec note et approbation; paire unique. |
| `Coupon` | Code de réduction, valeur, période et limites d'utilisation. |
| `ReturnRequest` | Demande liée en un-à-un à une commande. |
| `JumiaOrderTask` | Tâche associée à une ligne de commande; le modèle impose le mode simulation. |
| `JumiaOrderAttempt` | Historique d'une tâche; numéro de tentative unique par tâche. |

Les instantanés `OrderItem` préservent le nom et le prix historiques. Les changements de catalogue doivent les respecter.

## Authentification et permissions

- `AUTH_USER_MODEL='boutique.User'` active le modèle personnalisé.
- Les pages `/maison/` et l'admin Django personnalisé `/admin/` sont réservés aux superutilisateurs. Le code inspecté ne définit pas de rôles opérationnels granulaires.
- Les détails et reçus de commande sont accessibles au propriétaire de la commande ou au superutilisateur.
- Les mutations Web existantes passent par des requêtes POST protégées par CSRF.
- Le webhook Wave est exempté de CSRF, mais vérifie la signature du webhook.

## Opérations et outils actuels

Le dashboard propriétaire gère produits, collections, images, coupons, avis, retours, commandes, expédition et paiements. L'admin Django personnalisé enregistre notamment utilisateurs, produits avec galerie, catégories, commandes, coupons, avis, retours et tâches Jumia; les tâches Jumia y sont en lecture seule.

Commandes dans `boutique/management/commands/` :

- `check_database` : diagnostic de connexion.
- `migrate_sqlite_to_postgres` : utilitaire historique; ne pas utiliser pour l'architecture Supabase actuelle.
- `seed_store` : crée des données de démonstration; ne pas lancer en production.
- `queue_jumia_orders`, `run_jumia_agent`, `requeue_jumia_task` : traitement fournisseur avec simulation et contrôles.

Les scripts `scripts/postgres_backup.py` et `scripts/postgres_restore.py` sauvegardent et restaurent PostgreSQL; la restauration est destructive et requiert une confirmation.

Le depot contient `CatalogImportBatch`/`CatalogImportRow` et la commande `catalog_import`. `product_manager` gere les operations de catalogue approuvees. `order_manager` apporte les consultations et transitions auditees des commandes; aucun serveur MCP n'est encore present. Voir `docs/catalog-import.md`, `docs/product-manager.md` et `docs/order-manager.md`.

## Vérification de la production et état local

Le Blueprint `render.yaml` décrit un service Render Python, construit avec pip, servi par Gunicorn, et déclare les variables Supabase attendues. Les valeurs `sync:false` sont absentes du dépôt.

Le navigateur connecté à Render n'était pas accessible pendant cette mission; la configuration réellement enregistrée dans le tableau Render n'a donc pas pu être inspectée. Ce qui est établi est que le Blueprint versionné prévoit Supabase pour PostgreSQL et Storage. À vérifier dans Render, sans révéler les valeurs :

1. `DATABASE_URL` utilise bien l'hôte PostgreSQL du projet Supabase retenu;
2. les identifiants S3 sont présents et appartiennent au même projet;
3. le bucket et son accès correspondent au mode d'URL publique configuré.

Le `.env` local a été contrôlé sans afficher son contenu : `DATABASE_URL` est présent, mais n'est pas analysable comme une URL valide; les paramètres locaux de bucket, région et identifiants S3 sont absents. Ce constat est local uniquement; il ne prouve pas l'état de Render. Aucun `.env` n'a été modifié.

## Architecture cible de l'assistant opérationnel

1. Vérifier les réglages Render sans copier les secrets; réparer séparément le `.env` local si demandé.
2. Etendre les services bornes existants selon les besoins: importer le catalogue, gerer les produits et les commandes, puis ajouter des rapports operationnels.
3. Faire des mutations des opérations en aperçu par défaut, avec transaction DB, confirmation explicite à l'application, contrôles de concurrence et audit avant/après. S3 et PostgreSQL n'ayant pas une transaction commune, prévoir une compensation pour les fichiers.
4. Commencer les commandes en lecture seule; garder paiements, remboursements, suppressions et achats fournisseur sous validation humaine explicite.
5. Ajouter les skills de workflow pour appeler ces commandes documentées.
6. Ajouter ensuite un serveur MCP local avec outils en liste blanche et schémas stricts, qui réutilise les services et permissions. Ne pas exposer SQL, shell, secrets ou mutations ORM génériques.

Minimiser les données client retournées par les outils et inscrites dans les journaux. Valider les écritures automatisées sur une base et un bucket de test distincts avant toute mise en production.

## Gestionnaire de produits

`boutique/services/product_manager.py` et la commande `product_manager` gèrent le catalogue existant en PREVIEW/APPLY. Ils couvrent les prix et stocks simples ou CSV, les champs produit, les images principales, les recherches, les collections sans SKU et la publication explicite. Chaque APPLY exige l'empreinte de l'aperçu, verrouille les lignes concernées et écrit un journal `ProductChangeBatch` / `ProductChangeLog`. Voir `docs/product-manager.md`. La migration `0015_product_change_audit` doit être déployée avant l'utilisation distante; elle n'a pas été appliquée en production.

## Order Manager

`boutique/services/order_manager.py` et la commande `order_manager` exposent les consultations, statistiques, exports et transitions des commandes. Les transitions de paiement restent distinctes des transitions de fulfillment: le gestionnaire ne change jamais `payment_status`, n'envoie aucun courriel et n'effectue aucun paiement/remboursement. L'audit `OrderChangeBatch` / `OrderChangeLog` exige la migration `0016_order_change_audit`; voir `docs/order-manager.md`. Aucune migration distante ni donnée réelle n'a été touchée.

## Customer Manager

`boutique/services/customer_manager.py` et `customer_manager` donnent accès à la recherche, aux historiques/statistiques et aux exports de clients; `CustomerChangeBatch` / `CustomerChangeLog` auditent suspend/re-activate. La suspension réutilise `User.is_active`, bloque l'authentification des comptes ordinaires et interdit les comptes staff/superutilisateurs. La migration nécessaire est `0017_customer_change_audit`; voir `docs/customer-manager.md`. La commande est un outil local, sans session Django; l'accès au shell et aux identifiants de la base demeure le contrôle d'accès opérationnel.

## Dashboard Manager

`boutique/services/dashboard_manager.py` et la commande en lecture seule `dashboard_manager` calculent ventes, revenus enregistrés, comparaisons, rapports et stocks depuis les modèles existants. Seul `payment_status=confirmed` constitue une preuve de paiement; les commandes annulées sont exclues des ventes réalisées. Les lignes utilisent les prix historiques `OrderItem`, tandis que les collections sont attribuées selon la catégorie actuelle du produit faute d'instantané historique. La valeur du stock est une estimation au prix de vente courant, jamais un coût ou un bénéfice. Aucun modèle ni migration n'est ajouté. Voir `docs/dashboard-manager.md`.
