---
name: order-manager
description: Consulter et piloter les commandes Yorwani, analyser les paiements et préparer des transitions de fulfillment ou annulations avec aperçu et journal d'audit.
---

# Yorwani Order Manager

Lis `AGENTS.md` et `docs/order-manager.md` avant d'utiliser les commandes.

- Pour consulter, utilise `order_manager` (`list`, `detail`, `stats`, `export`, `history`). Les consultations sont en lecture seule.
- Pour modifier un statut, construis d'abord un `preview`; lis les commandes, articles, statuts de paiement, transitions proposées et erreurs. Ne considère jamais `payment_status` comme modifiable par cette compétence.
- Attends l'approbation explicite de l'utilisateur sur la liste exacte, la transition et l'empreinte avant APPLY. Ensuite, utilise la même sélection et les mêmes paramètres avec `--mode apply --confirm-digest <empreinte exacte>`.
- Pour une annulation, exige en plus une raison explicite et l'approbation claire de l'annulation; la commande doit recevoir `--reason ... --confirm-cancel ANNULER` avec l'empreinte approuvée. Ne propose ni n'exécute de remboursement.
- Si les données ont changé depuis l'aperçu, arrête l'APPLY, refais un aperçu et demande l'approbation du nouveau plan.
- N'utilise `--allow-remote-database` ni `--allow-production` qu'après demande explicite de travailler dans l'environnement distant; une simple demande d'implémentation locale ne l'autorise pas.
- Ne supprime aucune commande ni ligne `OrderItem`, ne change aucun prix historique et n'envoie aucun courriel client ou paiement depuis cette compétence.
- Après APPLY, rapporte les références, transitions, éventuels conflits et l'empreinte d'audit. Le journal se consulte avec `--action history --fingerprint <empreinte>`.

Pour les règles de transitions, les filtres, l'export et les exemples de lot CSV, consulte `docs/order-manager.md`.
