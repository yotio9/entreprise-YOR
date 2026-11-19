---
name: dashboard-manager
description: Consulter les ventes, revenus enregistrés, produits, collections et stocks de Yorwani avec des statistiques en lecture seule.
---

# Yorwani Dashboard Manager

Lis `AGENTS.md` et `docs/dashboard-manager.md` avant de produire un bilan.

- Utilise `python manage.py dashboard_manager` avec les actions `overview`, `sales`, `top-products`, `top-collections`, `revenue`, `compare`, `inventory` ou `report`.
- Les consultations sont en lecture seule. N'ajoute jamais d'action d'écriture ni de migration à ce workflow.
- N'interroge une base distante que si l'utilisateur demande explicitement les données distantes; les options `--allow-remote-database` et `--allow-production` sont des garde-fous, pas une permission implicite.
- Traite `payment_status=confirmed` comme seule preuve enregistrée d'un paiement. Exclue les commandes annulées des ventes réalisées; signale à part les paiements confirmés sur commande annulée, sans supposer de remboursement.
- Appelle les revenus chiffre d'affaires ou ventes enregistrées, jamais bénéfice. N'estime pas de marge. La valeur de stock fournie est au prix de vente courant, pas au coût.
- Précise que les prix de ventes article utilisent les snapshots `OrderItem` et que l'attribution aux collections dépend de la catégorie actuelle faute de snapshot.
- Respecte le fuseau Django, les bornes de période inclusives et les avertissements de données incohérentes. Si une valeur manque, indique-le sans la remplacer par une supposition.
- Pour CSV, choisis un chemin demandé ou approprié et ne remplace jamais un fichier existant.
