# Importer un catalogue Yorwani

L'importeur accepte actuellement un fichier CSV UTF-8 et un dossier d'images. Il utilise les modèles `Product` et `Category` existants; `Category` représente la collection. Il ne publie jamais un produit automatiquement.

## Préparer les fichiers

Le CSV doit inclure les colonnes suivantes (virgule ou point-virgule détecté automatiquement) :

```csv
name,sku,description,price,collection,stock,image_filename,is_active
Chemise Naya,YOR-001,Coton léger,12500,Essentiels,7,YOR-001.png,false
```

`sku` et `image_filename` peuvent rester vides. Pour les titres français, les noms de colonnes `nom du produit`, `SKU si disponible`, `description`, `prix`, `collection`, `stock`, `nom du fichier image` et `statut de publication souhaité` sont acceptés. Le CSV utilise UTF-8; les prix ont au plus deux décimales. Limites : 5 000 lignes, 25 Mo par CSV, 20 Mo par image.

Le champ `category` est aussi accepté pour la collection. Si le CSV fournit les deux champs, leurs valeurs doivent correspondre puisque le modèle actuel n'a qu'une seule relation `Product.category`.

Images acceptées : JPEG, PNG et WEBP. Quand `image_filename` est renseigné, le nom doit correspondre exactement à un fichier directement dans le dossier d'images. Quand il est vide, l'importeur cherche un nom de base exact correspondant au SKU ou au slug du produit. Plusieurs correspondances exactes, un fichier absent ou un format non pris en charge bloquent la ligne; aucune correspondance floue n'est faite. L'aperçu liste aussi les images du dossier qui ne sont pas utilisées. Limite totale des images référencées : 2 Go par import.

## 1. Prévisualiser

```powershell
python manage.py catalog_import --mode preview --file .\catalogue.csv --images .\images
```

PREVIEW lit la base configurée afin d'identifier les SKU déjà utilisés et les collections existantes. Il ne crée ni ne modifie de ligne en base, n'écrit aucune image et ne crée pas de batch. Par sécurité, une base distante exige l'option explicite `--allow-remote-database`; une configuration `DEBUG=0` exige aussi `--allow-production`. N'utilisez pas ces options pour un aperçu de production sans autorisation spécifique.

Le rapport montre les produits, les prix et stocks proposés, les images résolues, les collections à créer, les erreurs, les doublons et les demandes de publication. Il affiche aussi une empreinte calculée sur le CSV et les images associées.

## 2. Appliquer après approbation

Après avoir relu l'aperçu et approuvé son empreinte :

```powershell
python manage.py catalog_import --mode apply --file .\catalogue.csv --images .\images --confirm-digest EMPREINTE_A_APPROUVER
```

Les fichiers et le CSV sont relus; une modification depuis l'aperçu produit une empreinte différente et l'opération est refusée. Pour une base distante, ajouter `--allow-remote-database`. Pour une configuration `DEBUG=0`, ajouter également `--allow-production`. Ces options ne remplacent pas l'approbation explicite de l'utilisateur.

## Doublons et brouillons

- Les SKU sont nettoyés en majuscules et uniques lorsqu'ils sont fournis. Un SKU déjà utilisé est ignoré sans modifier le produit existant.
- Sans SKU, un produit du même nom dans la même collection rend la ligne ambiguë et bloquée; fournissez un SKU pour l'importer sans risque de doublon.
- Les collections absentes sont créées inactives. Les collections existantes sont conservées sans changement.
- Tous les produits créés ont `is_active=False`. La valeur `true` du fichier est enregistrée comme demande de publication dans le rapport d'import, mais ne publie rien. La publication doit être faite séparément depuis l'administration après autorisation.
- Les noms d'images dans Supabase Storage sont déterministes et dérivés du contenu. Un objet identique est réutilisé; une collision de contenu est signalée et jamais écrasée.

## Reprise et limites transactionnelles

APPLY conserve un batch et un résultat par ligne dans PostgreSQL. Les lignes réussies sont ignorées lors d'une reprise avec la même empreinte; les lignes en erreur sont retentées. Les erreurs de certaines lignes ne bloquent pas les autres lignes valides.

Chaque produit et sa ligne d'import sont enregistrés dans une transaction PostgreSQL par ligne. Supabase Storage ne partage pas cette transaction : si un upload réussit puis que la transaction DB échoue, l'objet reste dans le bucket et pourra être réutilisé lors d'une reprise. Le système ne supprime aucun objet automatiquement.

Le mode APPLY crée uniquement; il ne met pas à jour les produits existants et ne supprime aucune donnée. Corriger le fichier puis relancer PREVIEW génère un nouvel import contrôlé.
