from django.contrib.auth import get_user_model
from django.core import mail
from django.test import TestCase, override_settings
from django.urls import reverse

from .forms import ProductForm, RegistrationForm
from .models import Category, Order, Product

User = get_user_model()


class StorefrontTests(TestCase):
    def setUp(self):
        self.category = Category.objects.create(name='Prêt-à-porter')
        self.product = Product.objects.create(
            category=self.category,
            name='Robe Awa',
            description='Une silhouette contemporaine.',
            price='25000.00',
            sizes=['S', 'M'],
            stock=4,
        )
        self.customer = User.objects.create_user(
            username='client@example.com',
            email='client@example.com',
            password='MotDePasseSolide123!',
            first_name='Awa',
            last_name='Diallo',
        )
        self.admin = User.objects.create_superuser(
            username='PDGyann',
            email='owner@example.com',
            password='MotDePasseSolide123!',
        )

    def test_storefront_templates_render(self):
        urls = (
            reverse('accueil'),
            reverse('catalogue'),
            reverse('produit', args=[self.product.slug]),
            reverse('panier'),
        )
        for url in urls:
            with self.subTest(url=url):
                self.assertEqual(self.client.get(url).status_code, 200)

    def test_owner_dashboard_renders(self):
        self.client.force_login(self.admin)
        response = self.client.get(reverse('dashboard'))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Vue d’ensemble')

    def test_admin_order_pages_show_customer_delivery_location(self):
        order = Order.objects.create(
            user=self.customer,
            customer_name='Awa Diallo',
            customer_email=self.customer.email,
            phone='0712345678',
            address='Rue des Jardins, Riviera 3',
            city='Cocody, Abidjan',
            subtotal='25000.00',
        )
        self.client.force_login(self.admin)

        list_response = self.client.get(reverse('admin_commandes'))
        self.assertContains(list_response, 'Rue des Jardins, Riviera 3')
        self.assertContains(list_response, 'Cocody, Abidjan')

        detail_response = self.client.get(reverse('admin_commande_detail', args=[order.reference]))
        self.assertContains(detail_response, 'ADRESSE DE RÉSIDENCE / LIVRAISON')
        self.assertContains(detail_response, 'Rue des Jardins, Riviera 3')
        self.assertContains(detail_response, 'Cocody, Abidjan')

    @override_settings(EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend')
    def test_cash_on_delivery_is_saved_emailed_and_confirmed_by_admin(self):
        mail.outbox.clear()
        self.client.force_login(self.customer)
        session = self.client.session
        session['cart'] = {f'{self.product.pk}:M': {'product_id': self.product.pk, 'quantity': 1, 'size': 'M'}}
        session.save()

        checkout_page = self.client.get(reverse('commande'))
        self.assertContains(checkout_page, 'Wave maintenant')
        self.assertContains(checkout_page, 'Paiement à la livraison')

        response = self.client.post(reverse('commande'), {
            'phone': '0712345678',
            'address': 'Rue des Jardins',
            'city': 'Cocody, Abidjan',
            'payment_method': 'cash_on_delivery',
        })
        order = Order.objects.get(user=self.customer)
        self.assertRedirects(response, reverse('commande_detail', args=[order.reference]))
        self.assertEqual(order.payment_method, Order.PaymentMethod.CASH_ON_DELIVERY)
        self.assertEqual(order.payment_status, Order.PaymentStatus.DUE_ON_DELIVERY)
        self.assertEqual(order.status, Order.Status.CONFIRMED)
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(mail.outbox[0].to, [self.customer.email])
        self.assertIn('à régler au livreur', mail.outbox[0].body)
        self.assertIn('5 jours après votre commande', mail.outbox[0].body)

        customer_order = self.client.get(reverse('commande_detail', args=[order.reference]))
        self.assertContains(customer_order, 'PAIEMENT À LA LIVRAISON')
        self.assertContains(customer_order, 'Rue des Jardins')

        self.client.force_login(self.admin)
        admin_order = self.client.get(reverse('admin_commande_detail', args=[order.reference]))
        self.assertContains(admin_order, 'RÉSIDENCE / LIVRAISON')
        self.assertContains(admin_order, 'Paiement à la livraison')
        confirmation = self.client.post(reverse('admin_paiement_confirmer', args=[order.reference]))
        self.assertRedirects(confirmation, reverse('admin_commande_detail', args=[order.reference]))
        order.refresh_from_db()
        self.assertEqual(order.payment_status, Order.PaymentStatus.CONFIRMED)
        self.assertEqual(len(mail.outbox), 2)
        self.assertIn('reçu à la livraison', mail.outbox[1].body)

    def test_login_redirects_owner_to_dashboard_and_customer_to_next(self):
        owner_response = self.client.post(f"{reverse('connexion')}?next=/panier/", {
            'username': self.admin.email,
            'password': 'MotDePasseSolide123!',
        })
        self.assertRedirects(owner_response, reverse('dashboard'))

        self.client.logout()
        customer_response = self.client.post(f"{reverse('connexion')}?next=/collection/", {
            'username': self.customer.email,
            'password': 'MotDePasseSolide123!',
        })
        self.assertRedirects(customer_response, reverse('catalogue'))

    def test_admin_can_authenticate_with_email_when_username_is_different(self):
        response = self.client.post(reverse('connexion'), {
            'username': self.admin.email,
            'password': 'MotDePasseSolide123!',
        })
        self.assertRedirects(response, reverse('dashboard'))

    def test_account_name_is_visible_and_logout_shows_gold_confirmation(self):
        self.client.force_login(self.customer)
        account_page = self.client.get(reverse('historique_commandes'))
        self.assertContains(account_page, 'Awa Diallo')
        self.assertContains(account_page, 'boutique/css/account.css')
        self.assertContains(account_page, 'class="signout-button"')

        response = self.client.post(reverse('deconnexion'))
        self.assertRedirects(response, reverse('accueil'), fetch_redirect_response=False)
        home_page = self.client.get(reverse('accueil'))
        self.assertContains(home_page, 'Vous êtes bien déconnecté(e).')
        self.assertContains(home_page, 'message gold success')

    def test_owner_product_form_converts_size_list(self):
        form = ProductForm(data={
            'category': self.category.pk,
            'name': 'Ensemble Naya',
            'description': 'Une coupe nette.',
            'price': '45000',
            'sizes': 'S, M, XL',
            'stock': '5',
        })
        self.assertTrue(form.is_valid(), form.errors)
        product = form.save()
        self.assertEqual(product.sizes, ['S', 'M', 'XL'])
        edit_form = ProductForm(instance=product)
        self.assertEqual(edit_form['sizes'].value(), 'S, M, XL')

    def test_registration_requires_and_saves_a_ten_digit_phone(self):
        registration_page = self.client.get(reverse('inscription'))
        self.assertContains(registration_page, 'name="phone"')
        self.assertContains(registration_page, 'inputmode="numeric"')
        self.assertContains(registration_page, 'pattern="[0-9]{10}"')
        self.assertContains(registration_page, 'maxlength="10"')

        form_data = {
            'first_name': 'Mariam',
            'last_name': 'Kone',
            'email': 'mariam@example.com',
            'phone': '0712345678',
            'password1': 'YorwaniSecurePassword2026!',
            'password2': 'YorwaniSecurePassword2026!',
        }
        form = RegistrationForm(data=form_data)
        self.assertTrue(form.is_valid(), form.errors)
        user = form.save()
        self.assertEqual(user.phone, '0712345678')

        for invalid_phone in ('071234567', 'abcdefghij'):
            with self.subTest(phone=invalid_phone):
                form_data['email'] = f'{invalid_phone}@example.com'
                form_data['phone'] = invalid_phone
                self.assertFalse(RegistrationForm(data=form_data).is_valid())

    def test_catalogue_filters_can_be_combined(self):
        response = self.client.get(reverse('catalogue'), {
            'categorie': self.category.slug,
            'taille': 'M',
            'q': 'Robe',
        })
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Robe Awa')

    def test_customer_cannot_open_private_dashboard(self):
        self.client.force_login(self.customer)
        response = self.client.get(reverse('dashboard'))
        self.assertEqual(response.status_code, 302)

    def test_staff_without_superuser_cannot_open_django_admin(self):
        self.customer.is_staff = True
        self.customer.save(update_fields=['is_staff'])
        self.client.force_login(self.customer)
        response = self.client.get('/admin/')
        self.assertEqual(response.status_code, 302)

    def test_visitor_must_sign_in_to_checkout(self):
        session = self.client.session
        session['cart'] = {f'{self.product.pk}:M': {'product_id': self.product.pk, 'quantity': 1, 'size': 'M'}}
        session.save()
        response = self.client.get(reverse('commande'))
        self.assertEqual(response.status_code, 302)
        self.assertIn(reverse('connexion'), response.url)

    @override_settings(EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend')
    def test_order_reserves_stock_and_requires_admin_payment_confirmation(self):
        mail.outbox.clear()
        self.client.force_login(self.customer)
        session = self.client.session
        session['cart'] = {f'{self.product.pk}:M': {'product_id': self.product.pk, 'quantity': 2, 'size': 'M'}}
        session.save()
        response = self.client.post(reverse('commande'), {
            'phone': '+2250700000000',
            'address': 'Rue des Jardins',
            'city': 'Cocody, Abidjan',
            'payment_method': 'wave',
        })
        order = Order.objects.get(user=self.customer)
        self.assertRedirects(response, reverse('commande_detail', args=[order.reference]))
        self.product.refresh_from_db()
        self.assertEqual(self.product.stock, 2)
        self.assertEqual(order.subtotal, 50000)
        self.assertEqual(order.payment_status, Order.PaymentStatus.PENDING)
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(mail.outbox[0].to, [self.customer.email])
        self.assertIn(order.reference, mail.outbox[0].body)
        self.assertIn('5 jours', mail.outbox[0].body)
        self.assertIn('en attente de vérification', mail.outbox[0].body)

        order_page = self.client.get(reverse('commande_detail', args=[order.reference]))
        self.assertContains(order_page, 'estimée sous 5 jours')
        receipt_response = self.client.get(reverse('recu', args=[order.reference]))
        self.assertEqual(receipt_response.status_code, 409)

        self.client.force_login(self.admin)
        confirmation = self.client.post(reverse('admin_paiement_confirmer', args=[order.reference]))
        self.assertRedirects(confirmation, reverse('admin_commande_detail', args=[order.reference]))
        self.assertEqual(len(mail.outbox), 2)
        self.assertIn('Paiement confirmé', mail.outbox[1].subject)
        self.assertIn('commande Yorwani est maintenant confirmée', mail.outbox[1].body)
        self.assertIn('5 jours', mail.outbox[1].body)
        order.refresh_from_db()
        self.assertEqual(order.payment_status, Order.PaymentStatus.CONFIRMED)
        receipt_response = self.client.get(reverse('recu', args=[order.reference]))
        self.assertEqual(receipt_response.status_code, 200)

    def test_checkout_rejects_combined_sizes_over_available_stock(self):
        self.client.force_login(self.customer)
        session = self.client.session
        session['cart'] = {
            f'{self.product.pk}:S': {'product_id': self.product.pk, 'quantity': 3, 'size': 'S'},
            f'{self.product.pk}:M': {'product_id': self.product.pk, 'quantity': 2, 'size': 'M'},
        }
        session.save()
        response = self.client.post(reverse('commande'), {
            'phone': '+2250700000000',
            'address': 'Rue des Jardins',
            'city': 'Cocody, Abidjan',
            'payment_method': 'wave',
        })
        self.assertRedirects(response, reverse('panier'))
        self.assertFalse(Order.objects.filter(user=self.customer).exists())
        self.product.refresh_from_db()
        self.assertEqual(self.product.stock, 4)
