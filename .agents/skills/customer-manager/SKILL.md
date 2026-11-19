---
name: customer-manager
description: Rechercher les clients Yorwani, examiner leurs achats et préparer la suspension ou réactivation sécurisée de leur compte.
---

# Yorwani Customer Manager

Lis `AGENTS.md` et `docs/customer-manager.md` avant les mutations ou exports de clients.

- Utilise `customer_manager` (`list`, `detail`, `history`, `stats`, `top`, `export`) pour les lectures; ne requiers pas d'écriture pour consulter.
- Minimise l'exposition des coordonnées. N'affiche ni ne recherche de mot de passe, token ou secret. L'export historique est limité à un client et exclut ses coordonnées.
- Pour suspendre ou réactiver, affiche d'abord un PREVIEW avec identité, statut et résultat. Attends l'approbation explicite de la personne et de l'empreinte exacte.
- Une suspension doit inclure la raison demandée par l'utilisateur et nécessite `--confirm-suspension SUSPENDRE` lors de APPLY. Ne déduis pas la raison et n'applique pas un lot contenant des conflits.
- Une suspension/réactivation ne s'applique jamais à `is_staff` ou `is_superuser`. Si l'état change après PREVIEW, arrête l'APPLY et demande l'accord sur un nouvel aperçu.
- Après accord, réutilise exactement la même sélection et les mêmes options avec `--mode apply --confirm-digest <empreinte>`. Ne passe les options distantes qu'après une demande explicite de travailler sur la production; elles ne remplacent pas l'autorisation de l'opérateur.
- N'altère jamais les commandes, achats, montants ou identifiants de connexion. La suspension par `is_active=False` bloque l'authentification sans changer de mot de passe.
- Après mutation, rapporte les IDs, résultat et empreinte d'audit. L'audit se consulte avec `--action audit --fingerprint <empreinte>`.

Pour les schémas CSV, les filtres d'historique, les statistiques et les règles d'export, consulte `docs/customer-manager.md`.
