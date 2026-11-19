# Customer Manager Yorwani

`customer_manager` gère les comptes `boutique.User` existants. Les clients sont les comptes qui ne sont ni staff ni superutilisateurs. La recherche renvoie au plus 500 comptes; les exports ne sont pas tronqués.

## Consultation

```powershell
python manage.py customer_manager --action list
python manage.py customer_manager --action list --query "awa" --status active
python manage.py customer_manager --action list --date-from 2026-11-01 --date-to 2026-11-30
python manage.py customer_manager --action detail --customer-id 42
python manage.py customer_manager --action detail --email client@example.test
python manage.py customer_manager --action history --customer-id 42
python manage.py customer_manager --action history --email client@example.test --order-status delivered --date-from 2026-01-01
python manage.py customer_manager --action stats --date-from 2026-11-01 --date-to 2026-11-30
python manage.py customer_manager --action top --limit 20
```

La recherche couvre l'identifiant, le username, le nom/prénom, l'e-mail et le téléphone. Les résultats donnent le nombre de commandes (tous statuts), les achats payés, les dépenses nettes confirmées hors commandes annulées et la date du dernier achat payé.

Les statistiques décrivent tous les clients non privilégiés. `new_in_period` filtre les comptes par date d'inscription. La répartition d'activité est calculée selon le nombre d'achats payés non annulés: zéro, un, deux à cinq ou six et plus. `with_orders` comprend toute commande, y compris annulée. `top` classe les meilleurs clients par dépenses payées à vie, puis par nombre d'achats.

L'historique donne toutes les commandes du compte choisi, ses lignes, tailles, prix historiques, statuts et totaux. Il peut être filtré par statut et dates de création de commande. Il n'est pas possible d'exporter les commandes de tous les clients à la fois.

## Export CSV

```powershell
python manage.py customer_manager --action export --kind customers --status active --date-from 2026-11-01 --output .\clients.csv
python manage.py customer_manager --action export --kind orders --customer-id 42 --order-status delivered --date-from 2026-01-01 --output .\historique.csv
```

L'export clients contient identifiant, nom d'utilisateur, nom, e-mail, téléphone, état du compte, date d'inscription et statistiques d'achat. Il exclut mots de passe, tokens, adresse et notes de livraison. L'export commandes est limité au seul client sélectionné et n'inclut ni e-mail, téléphone ni adresse. Les filtres de dates et de statut s'appliquent au type d'export choisi. Les formules de tableur sont neutralisées et un fichier existant n'est jamais écrasé.

La commande est un outil local sans session Django. L'accès au terminal, au dépôt et aux identifiants de base est donc le contrôle d'accès de l'opérateur. Les options `--allow-remote-database` et `--allow-production` sont des garde-fous, pas une authentification. N'exportez les données distantes que depuis un environnement administrateur autorisé. Les détails et la recherche peuvent afficher des coordonnées nécessaires à la gestion du compte; les journaux d'audit ne les recopient pas.

## Suspendre ou réactiver

La suspension exige une raison de 5 à 500 caractères. PREVIEW affiche les comptes ciblés et le statut avant/après. Après validation explicite de la sélection et de l'empreinte, APPLY exige aussi la confirmation exacte `SUSPENDRE`:

```powershell
python manage.py customer_manager --action suspend --customer-id 42 --reason "Demande du client" --mode preview
python manage.py customer_manager --action suspend --customer-id 42 --reason "Demande du client" --mode apply --confirm-digest EMPREINTE --confirm-suspension SUSPENDRE
python manage.py customer_manager --action reactivate --customer-id 42 --mode preview
```

Après approbation de l'aperçu de réactivation, appliquez avec `--mode apply --confirm-digest EMPREINTE`. La suspension utilise `User.is_active=False`: le backend d'authentification Django refuse alors les prochaines authentifications et les sessions existantes seront rejetées à leur prochaine vérification d'utilisateur. La réactivation rétablit l'accès avec les identifiants existants. Aucun mot de passe ni token n'est changé.

Les comptes `is_staff` et `is_superuser` sont bloqués, y compris si leur statut change entre PREVIEW et APPLY. Les lots acceptent jusqu'à 1 000 IDs, e-mails ou lignes CSV (`id`, `customer_id` ou `email`). Un lot avec erreur est entièrement refusé. Exemple CSV:

```csv
email
client1@example.test
client2@example.test
```

## Audit et garanties

- PREVIEW n'écrit ni dans `User` ni dans l'audit.
- APPLY verrouille les comptes, compare l'état et les droits à l'aperçu, applique en transaction et refuse les plans périmés.
- Une empreinte réussie ne peut être appliquée deux fois; un retry retourne le résultat existant.
- `CustomerChangeBatch` et `CustomerChangeLog` conservent chaque suspension/réactivation, la raison et les valeurs avant/après. Le journal protège le compte contre suppression.
- Les commandes, lignes d'articles, montants historiques et comptes ne sont jamais supprimés. Aucune modification d'Order ou `OrderItem` n'est effectuée.
- Migration locale: `0017_customer_change_audit`, dépendante de `0016_order_change_audit`. Elle n'a pas été appliquée à une base distante.
