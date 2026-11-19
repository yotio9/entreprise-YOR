---
name: product-manager
description: Gérer le catalogue existant de Yorwani avec les commandes Django de recherche, prix, stocks, modifications et publication. Utiliser pour des demandes de mise à jour de produits, en lot ou depuis CSV.
---

# Yorwani Product Manager

Utilise les modèles et services existants de Yorwani. Lis `AGENTS.md` et `docs/product-manager.md` avant les mutations.

- Commence par `product_manager --action search` ou une commande de modification en mode `preview` (mode par défaut). N'utilise aucune base distante pour des vérifications locales.
- Présente les produits, anciennes et nouvelles valeurs, images, erreurs et conflits. N'invente pas de correspondances SKU ou de fichiers image.
- Pour toute modification de prix, stock ou publication, attends l'approbation explicite de l'utilisateur sur l'aperçu précis. Une demande de changement ne vaut pas approbation de APPLY.
- Après approbation, applique exactement la même commande et les mêmes sources avec `--mode apply --confirm-digest <empreinte affichée>`. Ne passe jamais `--allow-remote-database` ou `--allow-production` sans demande explicite visant l'environnement distant.
- Si la commande signale un plan périmé, n'essaie pas de forcer. Refais l'aperçu et demande l'approbation du nouveau plan.
- Pour publication, demande une approbation explicite qui dit clairement quels produits ou quelle collection seront publiés. `prepare-collection` signifie brouillon: cela désactive la collection et ses produits.
- Ne supprime jamais de produit ni de média. Ne modifie pas les commandes ni les instantanés de prix historiques. Ne révèle aucune variable d'environnement ou donnée client.
- Après APPLY, résume le résultat et l'empreinte; le journal est accessible avec `--action history --fingerprint <empreinte>`.

Pour les exemples CLI, le schéma CSV et les garanties de stockage, consulte `docs/product-manager.md`.
