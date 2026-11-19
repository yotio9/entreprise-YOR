# Order Manager Yorwani

La commande `order_manager` fournit des consultations en lecture seule et des transitions de commandes contrôlées. Elle ne modifie jamais les articles d'une commande, leurs prix historiques ou les champs de paiement. Elle n'envoie ni courriel client, ni paiement, ni remboursement.

## Consultation

```powershell
python manage.py order_manager --action list
python manage.py order_manager --action list --query YOR-ABC
python manage.py order_manager --action list --status awaiting_payment
python manage.py order_manager --action list --payment-status confirmed
python manage.py order_manager --action list --status shipped --date-from 2026-11-01 --date-to 2026-11-30
python manage.py order_manager --action detail --reference YOR-ABC
python manage.py order_manager --action stats
python manage.py order_manager --action export --date-from 2026-11-01 --output .\commandes.csv
python manage.py order_manager --action history --fingerprint EMPREINTE
```

La liste limite l'affichage à 500 commandes. `detail` affiche les coordonnées nécessaires au traitement de la commande et ses lignes. L'export contient le nom du client et sa ville, mais pas son adresse complète, son téléphone ni son courriel. L'export protège les cellules CSV contre les formules de tableur.

Les filtres sont combinables: `--query`, `--status`, `--payment-status`, `--date-from`, `--date-to`. Les dates sont inclusives au format `YYYY-MM-DD`. `--payment-status confirmed` permet de rechercher les commandes payées; `--status awaiting_payment`, `shipped` ou `cancelled` donne les files correspondantes.

Les statistiques indiquent le nombre de commandes, celles en attente, annulées, expédiées et livrées. Le chiffre d'affaires annoncé est la somme du total net (sous-total + livraison - remise, plancher à zéro) des commandes payées et non annulées. Les commandes annulées payées ne sont pas déduites comme remboursements, car aucun remboursement automatique n'est effectué.

## Transitions

Toutes les modifications commencent en `preview` (valeur par défaut). Examinez chaque commande, statut/paiement actuel, valeur proposée et erreur. Après approbation explicite, reprenez les mêmes paramètres avec `--mode apply --confirm-digest <empreinte exacte>`.

```powershell
python manage.py order_manager --action transition --reference YOR-ABC --status preparing
python manage.py order_manager --action transition --reference YOR-ABC --status shipped --carrier "Yorwani Express" --tracking-number TRACK-123
python manage.py order_manager --action transition --reference YOR-ABC --status delivered
python manage.py order_manager --action transition --csv selection.csv --status preparing
```

Le CSV de sélection doit contenir `reference` ou `order_reference`; une colonne Excel séparée par virgule, point-virgule ou tabulation est acceptée. Pour plusieurs commandes sans fichier, répétez `--reference`.

Transitions permises par le code métier actuel:

- `awaiting_payment` vers `confirmed` uniquement si le paiement est déjà confirmé;
- `awaiting_payment` vers `preparing` pour paiement confirmé, ou commande contre-remboursement (`cash_on_delivery`) à régler à la livraison;
- `confirmed` vers `preparing`, puis `preparing` vers `shipped`;
- `shipped` vers `delivered` uniquement si le paiement est confirmé.

Les transitions ne touchent jamais `payment_status`. Une commande Wave en attente ne peut pas être confirmée, préparée ou expédiée. Un contre-remboursement peut être préparé/expédié, mais ne peut être déclaré livré avant confirmation réelle de réception du paiement. Les statuts livré et annulé sont terminaux. À la livraison, les points de fidélité sont attribués selon la règle déjà appliquée par le dashboard, dans la même transaction.

## Annulation

L'annulation est une action séparée. La raison est obligatoire (5 à 500 caractères) et l'application exige une deuxième confirmation littérale `ANNULER` en plus de l'empreinte de PREVIEW:

```powershell
python manage.py order_manager --action cancel --reference YOR-ABC --reason "Annulation demandée par le client" --mode preview
python manage.py order_manager --action cancel --reference YOR-ABC --reason "Annulation demandée par le client" --mode apply --confirm-digest EMPREINTE --confirm-cancel ANNULER
```

Les commandes déjà expédiées ou livrées ne peuvent pas être annulées par cette commande. Une annulation ne marque pas le paiement comme remboursé et ne déclenche aucun remboursement; un éventuel remboursement doit suivre une procédure distincte avec preuve et autorisation.

## Audit et sécurité

- APPLY verrouille les commandes et lignes d'articles sélectionnées, vérifie leur état complet par rapport à PREVIEW et refuse tout plan devenu périmé.
- La migration ajoute `OrderChangeBatch` et `OrderChangeLog`; les valeurs avant/après et le motif d'annulation sont journalisés. Les journaux protègent les commandes contre suppression.
- Une reprise du même plan approuvé est idempotente. Une nouvelle prévisualisation d'un état différent a une autre empreinte.
- Aucun statut de paiement, ligne `OrderItem`, montant historique, achat, paiement ou remboursement n'est modifié.
- La commande bloque les bases distantes et `DEBUG=0` sauf présence de drapeaux explicites. Les drapeaux ne remplacent pas l'approbation humaine de l'aperçu.
- Les commandes et articles restent inchangés en PREVIEW. Les données d'audit ne stockent pas de coordonnées client.

Migration locale créée: `0016_order_change_audit`, dépendante de `0015_product_change_audit`. Elle doit être examinée et déployée par le processus normal avant d'utiliser APPLY dans une base distante.
