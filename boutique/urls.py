from django.urls import path

from . import views

urlpatterns = [
	path('', views.home, name='accueil'),
	path('collection/', views.catalogue, name='catalogue'),
	path('article/<slug:slug>/', views.product_detail, name='produit'),
	path('panier/', views.cart_detail, name='panier'),
	path('panier/ajouter/', views.add_to_cart, name='panier_ajouter'),
	path('panier/modifier/', views.update_cart, name='panier_modifier'),
	path('commande/', views.checkout, name='commande'),
	path('webhooks/wave/', views.wave_webhook, name='wave_webhook'),
	path('commande/<str:reference>/', views.order_detail, name='commande_detail'),
	path('commande/<str:reference>/recu/', views.receipt, name='recu'),
	path('inscription/', views.register, name='inscription'),
	path('connexion/', views.login_view, name='connexion'),
	path('deconnexion/', views.logout_view, name='deconnexion'),
	path('compte/commandes/', views.order_history, name='historique_commandes'),
	path('maison/', views.dashboard, name='dashboard'),
	path('maison/comptabilite/', views.admin_accounting, name='admin_comptabilite'),
	path('maison/articles/', views.admin_products, name='admin_articles'),
	path('maison/articles/nouveau/', views.product_create, name='admin_article_nouveau'),
	path('maison/articles/<int:product_id>/modifier/', views.product_edit, name='admin_article_modifier'),
	path('maison/articles/<int:product_id>/supprimer/', views.product_delete, name='admin_article_supprimer'),
	path('maison/images/<int:image_id>/supprimer/', views.product_image_delete, name='admin_image_supprimer'),
	path('maison/commandes/', views.admin_orders, name='admin_commandes'),
	path('maison/commandes/<str:reference>/', views.admin_order_detail, name='admin_commande_detail'),
	path('maison/commandes/<str:reference>/lien-wave/', views.send_wave_payment_link, name='admin_lien_wave'),
	path('maison/commandes/<str:reference>/confirmer/', views.confirm_payment, name='admin_paiement_confirmer'),
]