# Yorwani — instructions for coding agents

## Architecture de référence

- Backend : Django 5.2, application métier `boutique`, projet `yorwani`.
- Base de données : PostgreSQL hébergé sur Supabase. Render héberge Django; Supabase Storage héberge les médias de production.
- Ne pas introduire Neon, migrer la base, ni remplacer ou modifier la connexion PostgreSQL existante sans demande explicite.
- Les dépendances sont actuellement décrites dans `requirements.txt`; le déploiement Render utilise `pip`. Ne pas prétendre que `uv` est configuré tant que `pyproject.toml` et `uv.lock` ne sont pas présents.
- Lire `docs/architecture.md` et le code concerné avant toute modification métier. Le code est la source de vérité si la documentation devient obsolète.

## Sécurité et données

- Ne jamais afficher, copier, journaliser ou committer les valeurs de `.env` ou les secrets Render/Supabase. Pour diagnostiquer la configuration, rapporter uniquement présence, format ou fournisseur détecté, sans valeur ni chaîne de connexion.
- Ne jamais modifier des données de production, exécuter une commande de mutation contre la base de production, lancer `migrate`, `seed_store`, une restauration ou un import de production sans instruction explicite portant sur cette opération.
- Ne jamais supprimer des produits, images, commandes, utilisateurs, catégories ou fichiers de stockage sans demande explicite et aperçu de leurs conséquences.
- Les opérations qui touchent paiement, remboursement, commande, stock, prix, publication ou suppression doivent présenter les objets et valeurs concernés avant application. Ne pas confirmer un paiement Wave ni initier un achat fournisseur au nom de l'utilisateur.
- Minimiser l'exposition des coordonnées et autres données client. Ne pas inclure de données personnelles dans des outils d'agent ou journaux sans nécessité métier.
- Utiliser les formulaires, validations, services et permissions Django existants. Ne pas exposer une exécution SQL ou shell arbitraire à un futur outil MCP.

## Règles de modification

- Pour les opérations récurrentes, privilégier des services métier et commandes Django dédiés, à paramètres bornés, avec aperçu sans écriture par défaut et journal d'audit.
- Utiliser le stockage Django configuré pour les images; ne pas contourner `default_storage` avec des clés S3 codées en dur.
- Ne pas créer de migrations ou modifier les modèles pour une tâche documentaire ou de diagnostic.
- Ne pas lancer de tests ou de commandes ayant des effets de bord sauf demande explicite ou nécessité pour une modification autorisée. Toujours préciser les vérifications réellement effectuées.
- Garder les changements ciblés et expliquer les fichiers modifiés, les limites de vérification et les risques opérationnels.

## Configuration distante

Le dépôt configure `DATABASE_URL` comme variable Render non synchronisée (`sync:false`), donc son hôte réel n'est pas visible dans Git. Avant un travail nécessitant une vérification de production, confirmer dans Render que cette variable pointe vers PostgreSQL Supabase et que les variables S3 correspondent au projet et au bucket attendus, sans afficher leurs valeurs. La seule lecture de `render.yaml` ne vérifie pas les réglages du tableau Render.
