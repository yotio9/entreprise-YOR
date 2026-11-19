# Dashboard Manager Yorwani

La commande `dashboard_manager` est un outil de consultation uniquement. Elle ne modifie aucun modèle, aucune donnée ni aucun journal. Elle refuse par défaut les bases distantes et `DEBUG=0`; ces garde-fous ne remplacent pas l'autorisation de l'opérateur. Les options de déverrouillage ne doivent être utilisées que pour une demande explicite de consulter la base concernée.

## Exemples

```powershell
python manage.py dashboard_manager --action overview
python manage.py dashboard_manager --action sales --period month --granularity day
python manage.py dashboard_manager --action top-products --period month --limit 10
python manage.py dashboard_manager --action top-products --sort least --period month --limit 10
python manage.py dashboard_manager --action top-collections --period month
python manage.py dashboard_manager --action revenue --period month
python manage.py dashboard_manager --action compare --period month
python manage.py dashboard_manager --action inventory
python manage.py dashboard_manager --action report --report-type collection --collection "Essentiels" --period month
python manage.py dashboard_manager --action report --report-type product --product-id 42 --period month
python manage.py dashboard_manager --action sales --date-from 2026-11-01 --date-to 2026-11-18 --format csv --output .\ventes.csv
```

Les périodes sont `day`, `week`, `month` et `all`; une période personnalisée exige les deux bornes `--date-from` et `--date-to` inclusives au format `YYYY-MM-DD`. Le fuseau est celui configuré par Django. La semaine commence le lundi. `compare --period month` compare le mois calendaire courant au mois précédent; les autres comparaisons utilisent la durée précédente équivalente. Si la base de comparaison vaut zéro, le pourcentage est `null` plutôt qu'un chiffre artificiel.

`overview` montre les ventes encaissées enregistrées de toute la période, les ventes du jour/semaine/mois, commandes par statut, clients, produits actifs et stocks. Les nouveaux clients sont comptés selon la période demandée (par défaut le mois).

## Définitions financières

- Une vente réalisée est une commande dont le statut de paiement enregistré est `confirmed` et dont la commande n'est pas annulée. La preuve est le statut réellement stocké; la commande n'en crée aucune.
- Les montants de commande sont calculés depuis `subtotal + shipping_fee - discount_amount`, avec un plancher à zéro. Les revenus de ventes ne sont ni un bénéfice ni une marge.
- Les commandes non annulées `pending` ou `due_on_delivery` sont rapportées séparément comme paiements en attente. La distribution inclut les statuts réellement présents en base et sépare les commandes annulées.
- Les paiements confirmés sur commandes annulées sont signalés séparément, sans déduire un remboursement: aucun statut de remboursement n'est disponible.
- Les unités et revenus par produit utilisent `OrderItem.quantity`, `product_name` et le prix unitaire historique. Le total financier reste celui enregistré sur `Order`. Un contrôle signale les commandes dont les articles manquent ou dont la somme des lignes diffère du sous-total.
- Les ventes par collection utilisent la catégorie actuelle du produit. L'historique ne conserve pas la collection au moment de l'achat; les chiffres sont donc attribués selon la relation actuelle, avec cette limite signalée.
- Aucune donnée de coût d'achat n'est disponible. Le système n'estime ni marge ni bénéfice. La valeur du stock est `stock × prix de vente actuel` pour les produits actifs; elle est explicitement une estimation de valeur de vente, pas une valeur au coût.

Les commandes CSV sont protégées contre les formules de tableur et refusent d'écraser un fichier existant. Elles produisent une ligne normalisée par mesure ou enregistrement. Aucun export n'inclut de coordonnées client.

Aucune migration n'est nécessaire. Les tests du service utilisent des données isolées locales.
