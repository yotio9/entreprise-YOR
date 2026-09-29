# Yorwani

Boutique Django pour la maison de mode Yorwani. Le projet utilise SQLite, des templates Django et Pillow pour les images téléversées.

## Démarrage local (Windows PowerShell)

```powershell
.\.venv\Scripts\Activate.ps1
python manage.py migrate
python manage.py seed_store
python manage.py createsuperuser
python manage.py runserver
```

Ouvrir `http://127.0.0.1:8000`. L’espace propriétaire est sur `/maison/` et l’administration Django est sur `/admin/`. Les deux sont réservés au superutilisateur créé avec `createsuperuser`.

Première installation :

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
python manage.py migrate
python manage.py seed_store
python manage.py createsuperuser
```

La commande `seed_store` ajoute des catégories et pièces d’exemple, que le propriétaire peut modifier ou supprimer. Les images d’exemple sont chargées depuis Unsplash ; les photos définitives se téléversent depuis la fiche article.

## Logo et configuration

Le logo n’est pas fourni avec le projet. Définir `BRAND_LOGO_URL` avec une URL accessible ou remplacer le texte Yorwani dans `templates/boutique/base.html`. Le fichier local `.env` est chargé automatiquement et ignoré par Git. Pour Gmail, renseigne `EMAIL_HOST_USER` avec l’adresse Gmail de la boutique, `EMAIL_HOST_PASSWORD` avec son mot de passe d’application Google, et `DEFAULT_FROM_EMAIL` avec cette même adresse. `EMAIL_HOST`, le port 587 et TLS sont déjà configurés. Active la validation en deux étapes du compte Google puis crée un mot de passe d’application dans ses paramètres de sécurité ; utilise ce code dans `.env`, jamais le mot de passe habituel. Redémarre le serveur après modification. Les e-mails partent au client (`Order.customer_email`), pas à l’adresse expéditrice.

```powershell
$env:DJANGO_SECRET_KEY = python -c "from django.core.management.utils import get_random_secret_key; print(get_random_secret_key())"
$env:DJANGO_DEBUG = "1"
$env:DJANGO_ALLOWED_HOSTS = "localhost,127.0.0.1"
$env:BRAND_LOGO_URL = "https://votre-domaine/logo.png"
```

`WAVE_MERCHANT_NUMBER` vaut par défaut `0171373056`. `WAVE_QR_IMAGE` peut pointer vers l’image du QR marchand. En l’absence d’identifiants API Wave fournis, la boutique crée une commande et affiche le numéro, le QR facultatif et le montant à transférer. Le paiement reste en attente ; le propriétaire le confirme après vérification réelle dans Wave. Aucun transfert ni reçu n’est simulé.

## E-mails de commande

À la création de commande, un accusé de réception est envoyé automatiquement au client. Il précise que le paiement Wave reste à vérifier et que la livraison est estimée sous 5 jours après confirmation du paiement. Quand le propriétaire confirme le transfert dans l’administration, un second e-mail confirme la commande et rappelle le délai estimé. En développement (`DEBUG=1`), les messages sont imprimés dans le terminal et ne sont pas expédiés. Pour l’envoi réel, configurer un fournisseur SMTP avec `EMAIL_BACKEND=django.core.mail.backends.smtp.EmailBackend`, `EMAIL_HOST`, `EMAIL_PORT`, `EMAIL_USE_TLS`, `EMAIL_HOST_USER`, `EMAIL_HOST_PASSWORD` et une adresse `DEFAULT_FROM_EMAIL` autorisée par ce fournisseur. Pour Gmail, utiliser un mot de passe d’application, jamais le mot de passe habituel du compte. Les variables d’environnement ne sont pas chargées automatiquement depuis `.env`.

## Modèle de données

- `User` : compte client avec téléphone à 10 chiffres et rôle propriétaire via les permissions superutilisateur Django.
- `Category` : catégories du catalogue.
- `Product` : prix, tailles JSON, stock, matière, entretien et image principale.
- `ProductImage` : galerie d’images associées à un article.
- `Order` : client, coordonnées de livraison, référence, montants et statuts commande/paiement.
- `OrderItem` : nom, taille, quantité et prix unitaire figés au moment de la commande.

Le checkout propose `Wave maintenant` ou `Paiement à la livraison`. Le premier reste en attente jusqu’à vérification du transfert par le propriétaire ; le second est marqué « à régler à la livraison ». Dans les deux cas, la méthode choisie figure sur la commande et dans l’e-mail. Pour le paiement à la livraison, l’admin confirme l’encaissement après remise au client ; cette action débloque le reçu et l’e-mail de paiement reçu.

## Parcours et accès

Les visiteurs parcourent la collection et ajoutent au panier ; un compte est requis avant de valider la commande. Téléphone, ville et adresse sont obligatoires. Le stock est revalidé et réservé côté serveur à la création de la commande. Le reçu n’est accessible qu’au client propriétaire de la commande, après confirmation du paiement par un superutilisateur.

L’administration publique du catalogue n’existe pas : les écritures passent par le dashboard propriétaire ou l’admin Django personnalisé, tous deux réservés aux superutilisateurs. Les mutations utilisent POST avec protection CSRF.

## Vérifications

```powershell
python manage.py check
python manage.py test boutique
python manage.py makemigrations --check --dry-run
```

Pour une mise en production, utiliser HTTPS, une clé secrète dédiée, `DJANGO_DEBUG=0`, une liste `DJANGO_ALLOWED_HOSTS` exacte, et un serveur de fichiers adapté aux médias. La configuration active la redirection HTTPS et HSTS lorsque `DEBUG` est désactivé. SQLite convient au démarrage local ; un déploiement à fort trafic mérite une base PostgreSQL.# entreprise-YOR
