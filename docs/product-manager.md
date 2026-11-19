# Gestion des produits Yorwani

La commande `product_manager` réutilise les modèles `Product` et `Category`. Toutes les modifications commencent en `preview`; aucune mutation n'est exécutée sans `apply` et l'empreinte exacte de l'aperçu.

## Règle d'approbation

1. Lancez l'action avec `--mode preview` et examinez tous les objets, valeurs avant/après, erreurs et conflits.
2. Après approbation explicite des changements affichés, relancez la même commande avec `--mode apply --confirm-digest <empreinte>`.
3. En cas d'écart aux données depuis l'aperçu, l'application est refusée. Refaites un aperçu et demandez une nouvelle approbation.
4. Le journal est consultable par empreinte avec `--action history --fingerprint <empreinte>`.

`preview` est le mode par défaut. La commande refuse une base distante sans `--allow-remote-database`, et `DEBUG=0` sans `--allow-production`. Ces options n'autorisent pas à elles seules une mutation: l'empreinte approuvée reste obligatoire.

## Rechercher et contrôler le catalogue

```powershell
python manage.py product_manager --action search --query "chemise"
python manage.py product_manager --action search --collection "Essentiels" --missing-sku
python manage.py product_manager --action search --out-of-stock
python manage.py product_manager --action search --low-stock-threshold 3 --visibility published
```

La recherche affiche au plus 1 000 produits par commande. Le seuil faible inclut les stocks de 1 à la valeur donnée; la rupture est strictement égale à zéro.

## Prix et stocks

```powershell
python manage.py product_manager --action set-price --sku YOR-001 --price 12500 --mode preview
python manage.py product_manager --action set-price --collection "Essentiels" --percent -10 --mode preview
python manage.py product_manager --action set-stock --product-id 42 --stock 8 --mode preview
python manage.py product_manager --action set-price --csv prix.csv --mode preview
python manage.py product_manager --action set-stock --csv stocks.csv --mode preview
```

CSV prix: colonnes `sku` ou `id`, plus `price`. CSV stock: `sku` ou `id`, plus `stock`. Le séparateur virgule, point-virgule ou tabulation est détecté. Valeurs décimales avec point et au plus deux décimales; stock entier positif ou nul. Les lignes identifiant un même produit plusieurs fois bloquent APPLY.

## Champs produit et image principale

```powershell
python manage.py product_manager --action update --sku YOR-001 --name "Chemise Malia" --description "Description" --target-collection "Essentiels" --mode preview
python manage.py product_manager --action update-csv --csv modifications.csv --images .\images --mode preview
```

Le CSV de mise à jour accepte `id` ou `sku`, puis `name`, `description`, `price`, `stock`, `collection` et `image_filename`. L'image doit être un fichier JPEG, PNG ou WEBP directement dans le dossier `--images`; le rapprochement est exact. La colonne de publication est interdite: utilisez les actions dédiées. Les nouvelles images passent par le stockage Django et ont un nom dérivé de leur empreinte de contenu. Elles ne remplacent jamais un objet existant; aucun fichier n'est supprimé.

## Publication

```powershell
python manage.py product_manager --action publish --sku YOR-001 --mode preview
python manage.py product_manager --action unpublish --collection "Essentiels" --mode preview
python manage.py product_manager --action publish --csv selection.csv --mode preview
python manage.py product_manager --action prepare-collection --collection "Essentiels" --mode preview
```

`selection.csv` comporte `sku` ou `id`. `prepare-collection` désactive les produits de la collection et la collection elle-même; cela ne publie rien. La publication n'a lieu qu'après aperçu et accord explicite avec l'empreinte correspondante.

## Garanties et limites

- Les produits et catégories sont verrouillés dans une transaction, puis leur état est comparé à celui de l'aperçu. Une concurrence invalide le lot.
- La même empreinte APPLY est idempotente: une reprise après succès ne crée pas de second journal.
- Le journal conserve les valeurs avant/après; les lignes `OrderItem` et leurs prix historiques ne sont pas modifiés.
- Un CSV est traité de façon atomique côté base: une ligne invalide bloque l'ensemble du lot, avec les erreurs affichées en aperçu.
- Le stockage objet et PostgreSQL n'ont pas de transaction commune. En cas de conflit DB après upload, une image immuable non référencée peut rester dans le bucket; le système ne l'efface pas automatiquement.
- La migration d'audit doit être déployée selon le processus du projet avant usage dans une base distante. Aucune migration de production n'est exécutée par cette fonctionnalité.
