---
name: catalog-importer
description: Importer un catalogue Yorwani depuis un CSV et un dossier d'images, prévisualiser les produits et préparer un import contrôlé. Utiliser pour les imports ou mises à jour de catalogue depuis CSV.
---

# Yorwani Catalog Importer

Lis `AGENTS.md` et `docs/catalog-import.md` avant un import.

- Vérifie le CSV et le dossier d'images, puis lance `catalog_import --mode preview` avant toute application. L'aperçu liste les produits, prix, stocks, collections, images, erreurs et demandes de publication sans écrire en base ni dans le stockage.
- Présente l'aperçu et son empreinte à l'utilisateur. N'importe rien et ne modifie aucun produit sans son approbation explicite du contenu et de l'empreinte exacte.
- Après approbation, applique le même CSV et le même dossier avec `--mode apply --confirm-digest <empreinte approuvée>`. Si les fichiers ont changé, arrête-toi et refais un aperçu.
- N'utilise jamais `--allow-remote-database` ou `--allow-production` sans demande explicite portant sur l'environnement distant ou de production. Une vérification locale n'autorise pas une écriture distante.
- L'import crée seulement de nouveaux produits; un SKU déjà utilisé est ignoré et aucun produit existant n'est mis à jour ou supprimé. Les nouveaux produits et collections restent en brouillon. Une demande de publication dans le CSV ne publie rien.
- Ne renomme, ne remplace ni ne supprime aucun fichier image. N'invente pas de correspondance d'image; les correspondances automatiques doivent être exactes par SKU ou slug.
- Ne lance aucune migration, commande de restauration ou import de production dans le cadre de cette compétence. Ne révèle aucune variable d'environnement, secret ni donnée client.
- Après APPLY, rapporte le résultat et les erreurs par ligne. Pour un import partiel, corrige la source puis refais PREVIEW avant toute reprise.

Pour le format CSV, les limites, la reprise et les détails de stockage, consulte `docs/catalog-import.md`.
