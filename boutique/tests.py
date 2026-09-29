import hashlib
import hmac
import json
import time
from unittest.mock import MagicMock, patch

from django.contrib.auth import get_user_model
from django.core import mail
from django.test import TestCase, override_settings
from django.urls import reverse

from .forms import ProductForm, RegistrationForm
from .models import Category, Order, Product
from .wave import create_checkout_session, create_merchant_payment_link

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
                response = self.client.get(url)
                self.assertEqual(response.status_code, 200)
                self.assertContains(response, 'boutique/img/favicon.png')
                self.assertContains(response, 'boutique/img/logo-nom-transparent.png')
                self.assertContains(response, 'boutique/img/logo-entier-transparent.png')

    def test_owner_dashboard_renders(self):
        self.client.force_login(self.admin)
        response = self.client.get(reverse('dashboard'))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Vue d’ensemble')

    def test_admin_accounting_totals_received_and_outstanding_money(self):
        base_order = {
            'user': self.customer,
            'customer_name': 'Awa Diallo',
            'customer_email': self.customer.email,
            'phone': '0712345678',
            'address': 'Rue des Jardins',
            'city': 'Cocody, Abidjan',
        }
        paid_order = Order.objects.create(
            **base_order,
            payment_status=Order.PaymentStatus.CONFIRMED,
            subtotal='30000.00',
            shipping_fee='1000.00',
        )
        wave_pending_order = Order.objects.create(
            **base_order,
            payment_status=Order.PaymentStatus.PENDING,
            payment_method=Order.PaymentMethod.WAVE,
            subtotal='15000.00',
            shipping_fee='1000.00',
        )
        cash_due_order = Order.objects.create(
            **base_order,
            payment_status=Order.PaymentStatus.DUE_ON_DELIVERY,
            payment_method=Order.PaymentMethod.CASH_ON_DELIVERY,
            subtotal='5000.00',
            shipping_fee='500.00',
        )
        cancelled_order = Order.objects.create(
            **base_order,
            payment_status=Order.PaymentStatus.CONFIRMED,
            status=Order.Status.CANCELLED,
            subtotal='80000.00',
        )
        self.client.force_login(self.admin)

        response = self.client.get(reverse('admin_comptabilite'))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, '31000 FCFA')
        self.assertContains(response, '16000 FCFA')
        self.assertContains(response, '5500 FCFA')
        self.assertContains(response, '21500 FCFA')
        self.assertContains(response, wave_pending_order.reference)
        self.assertContains(response, cash_due_order.reference)
        self.assertNotContains(response, cancelled_order.reference)

    def test_customer_cannot_view_admin_accounting(self):
        self.client.force_login(self.customer)

        response = self.client.get(reverse('admin_comptabilite'))

        self.assertEqual(response.status_code, 302)
        self.assertIn(reverse('connexion'), response.url)

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
    def test_admin_can_send_wave_generated_payment_link_to_customer(self):
        mail.outbox.clear()
        order = Order.objects.create(
            user=self.customer,
            customer_name='Awa Diallo',
            customer_email=self.customer.email,
            phone='0712345678',
            address='Rue des Jardins',
            city='Cocody, Abidjan',
            subtotal='25000.00',
        )
        self.client.force_login(self.admin)
        payment_url = 'https://pay.wave.com/c/merchant-generated-link?a=25000&c=XOF'

        response = self.client.post(reverse('admin_lien_wave', args=[order.reference]), {
            'wave_payment_url': payment_url,
        })

        self.assertRedirects(response, reverse('admin_commande_detail', args=[order.reference]))
        order.refresh_from_db()
        self.assertEqual(order.wave_launch_url, payment_url)
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(mail.outbox[0].to, [self.customer.email])
        self.assertIn(payment_url, mail.outbox[0].body)
        self.assertIn('25000 FCFA', mail.outbox[0].body)
        self.assertNotIn('/maison/commandes/', mail.outbox[0].body)

    @override_settings(EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend')
    def test_admin_payment_link_rejects_non_wave_domains(self):
        order = Order.objects.create(
            user=self.customer,
            customer_name='Awa Diallo',
            customer_email=self.customer.email,
            phone='0712345678',
            address='Rue des Jardins',
            city='Cocody, Abidjan',
            subtotal='25000.00',
        )
        self.client.force_login(self.admin)

        response = self.client.post(reverse('admin_lien_wave', args=[order.reference]), {
            'wave_payment_url': 'https://example.com/fake-wave-payment',
        })

        self.assertRedirects(response, reverse('admin_commande_detail', args=[order.reference]))
        order.refresh_from_db()
        self.assertFalse(order.wave_launch_url)
        self.assertEqual(len(mail.outbox), 0)

    @override_settings(
        EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend',
        ORDER_NOTIFICATION_EMAIL='owner@example.com',
    )
    def test_cash_on_delivery_is_saved_emailed_and_confirmed_by_admin(self):
        mail.outbox.clear()
        self.client.force_login(self.customer)
        session = self.client.session
        session['cart'] = {f'{self.product.pk}:M': {'product_id': self.product.pk, 'quantity': 1, 'size': 'M'}}
        session.save()

        checkout_page = self.client.get(reverse('commande'))
        self.assertContains(checkout_page, 'Wave par lien e-mail')
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
        self.assertEqual(len(mail.outbox), 2)
        self.assertEqual(mail.outbox[0].to, [self.customer.email])
        self.assertIn('à régler au livreur', mail.outbox[0].body)
        self.assertIn('5 jours après votre commande', mail.outbox[0].body)
        self.assertIn('http://testserver/', mail.outbox[0].body)
        self.assertIn(f'http://testserver/commande/{order.reference}/', mail.outbox[0].body)
        self.assertIn('http://testserver/compte/commandes/', mail.outbox[0].body)
        self.assertEqual(mail.outbox[1].to, ['owner@example.com'])
        self.assertIn(order.reference, mail.outbox[1].subject)
        self.assertIn(self.customer.email, mail.outbox[1].body)
        self.assertIn('Robe Awa', mail.outbox[1].body)

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
        self.assertEqual(len(mail.outbox), 3)
        self.assertIn('reçu à la livraison', mail.outbox[2].body)

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

    @override_settings(WAVE_API_KEY='wave_test_key', WAVE_SITE_URL='https://yorwani.example')
    @patch('boutique.wave.urlopen')
    def test_wave_checkout_session_uses_order_amount_and_https_return_urls(self, mock_urlopen):
        order = Order.objects.create(
            user=self.customer,
            customer_name='Awa Diallo',
            customer_email=self.customer.email,
            phone='0712345678',
            address='Rue des Jardins',
            city='Cocody, Abidjan',
            subtotal='25000.00',
        )
        order.refresh_from_db()
        response = MagicMock()
        response.__enter__.return_value.read.return_value = json.dumps({
            'id': 'cos-test-checkout',
            'wave_launch_url': 'https://pay.wave.com/c/cos-test-checkout',
        }).encode()
        mock_urlopen.return_value = response

        checkout_session = create_checkout_session(order)

        self.assertEqual(checkout_session['id'], 'cos-test-checkout')
        self.assertEqual(checkout_session['wave_launch_url'], 'https://pay.wave.com/c/cos-test-checkout')
        api_request = mock_urlopen.call_args.args[0]
        payload = json.loads(api_request.data)
        self.assertEqual(payload['amount'], '25000')
        self.assertEqual(payload['currency'], 'XOF')
        self.assertEqual(payload['client_reference'], order.reference)
        self.assertTrue(payload['success_url'].startswith('https://yorwani.example/'))
        self.assertTrue(payload['error_url'].startswith('https://yorwani.example/'))
        self.assertEqual(api_request.get_header('Authorization'), 'Bearer wave_test_key')

    def test_wave_merchant_link_replaces_sample_amount(self):
        link = create_merchant_payment_link(
            'https://pay.wave.com/m/M_ci_zp9lcefif5t-/c/ci/?amount=500',
            '37500.00',
        )

        self.assertEqual(link, 'https://pay.wave.com/m/M_ci_zp9lcefif5t-/c/ci/?amount=37500')

    @override_settings(
        EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend',
        ORDER_NOTIFICATION_EMAIL='owner@example.com',
        WAVE_API_KEY='',
        WAVE_PAYMENT_LINK_BASE='https://pay.wave.com/m/M_ci_zp9lcefif5t-/c/ci/?amount=500',
    )
    def test_wave_order_uses_merchant_link_with_order_amount(self):
        mail.outbox.clear()
        self.client.force_login(self.customer)
        session = self.client.session
        session['cart'] = {f'{self.product.pk}:M': {'product_id': self.product.pk, 'quantity': 1, 'size': 'M'}}
        session.save()

        response = self.client.post(reverse('commande'), {
            'phone': '0712345678',
            'address': 'Rue des Jardins',
            'city': 'Cocody, Abidjan',
            'payment_method': 'wave',
        })

        order = Order.objects.get(user=self.customer)
        expected_link = 'https://pay.wave.com/m/M_ci_zp9lcefif5t-/c/ci/?amount=25000'
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response['Location'], reverse('commande_detail', args=[order.reference]))
        order.refresh_from_db()
        self.assertEqual(order.wave_launch_url, expected_link)
        self.assertFalse(order.wave_checkout_id)
        self.assertEqual(order.payment_status, Order.PaymentStatus.PENDING)
        self.assertIn(expected_link, mail.outbox[0].body)
        self.assertIn('sous 5 jours', mail.outbox[0].body)
        payment_page = self.client.get(reverse('commande_detail', args=[order.reference]))
        self.assertContains(payment_page, 'envoyé par e-mail')
        self.assertNotContains(payment_page, 'Ouvrir Wave et payer')

    @override_settings(
        EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend',
        ORDER_NOTIFICATION_EMAIL='owner@example.com',
        WAVE_API_KEY='wave_test_key',
        WAVE_SITE_URL='https://yorwani.example',
    )
    @patch('boutique.views.create_checkout_session')
    def test_wave_order_redirects_to_app_and_sends_checkout_link(self, mock_create_session):
        mock_create_session.return_value = {
            'id': 'cos-test-redirect',
            'wave_launch_url': 'https://pay.wave.com/c/cos-test-redirect',
        }
        mail.outbox.clear()
        self.client.force_login(self.customer)
        session = self.client.session
        session['cart'] = {f'{self.product.pk}:M': {'product_id': self.product.pk, 'quantity': 1, 'size': 'M'}}
        session.save()

        response = self.client.post(reverse('commande'), {
            'phone': '0712345678',
            'address': 'Rue des Jardins',
            'city': 'Cocody, Abidjan',
            'payment_method': 'wave',
        })

        order = Order.objects.get(user=self.customer)
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response['Location'], reverse('commande_detail', args=[order.reference]))
        order.refresh_from_db()
        self.assertEqual(order.wave_checkout_id, 'cos-test-redirect')
        self.assertIn(order.wave_launch_url, mail.outbox[0].body)
        payment_page = self.client.get(reverse('commande_detail', args=[order.reference]))
        self.assertContains(payment_page, 'envoyé par e-mail')
        self.assertNotContains(payment_page, 'Ouvrir Wave et payer')

    @override_settings(
        EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend',
        WAVE_WEBHOOK_SECRET='wave_webhook_test_secret',
    )
    def test_signed_wave_webhook_confirms_payment_and_sends_one_email(self):
        order = Order.objects.create(
            user=self.customer,
            customer_name='Awa Diallo',
            customer_email=self.customer.email,
            phone='0712345678',
            address='Rue des Jardins',
            city='Cocody, Abidjan',
            subtotal='25000.00',
            wave_checkout_id='cos-webhook-test',
        )
        event_body = json.dumps({
            'id': 'event-test',
            'type': 'checkout.session.completed',
            'data': {
                'id': 'cos-webhook-test',
                'amount': '25000',
                'currency': 'XOF',
                'payment_status': 'succeeded',
                'checkout_status': 'complete',
                'transaction_id': 'wave-transaction-test',
            },
        }, separators=(',', ':')).encode()
        timestamp = str(int(time.time()))
        signature = hmac.new(
            b'wave_webhook_test_secret',
            timestamp.encode() + event_body,
            hashlib.sha256,
        ).hexdigest()
        headers = {'HTTP_WAVE_SIGNATURE': f't={timestamp},v1={signature}'}
        mail.outbox.clear()

        invalid_response = self.client.post(
            reverse('wave_webhook'),
            data=event_body,
            content_type='application/json',
            HTTP_WAVE_SIGNATURE=f't={timestamp},v1=invalid',
        )
        self.assertEqual(invalid_response.status_code, 401)
        order.refresh_from_db()
        self.assertEqual(order.payment_status, Order.PaymentStatus.PENDING)

        response = self.client.post(reverse('wave_webhook'), data=event_body, content_type='application/json', **headers)
        order.refresh_from_db()

        self.assertEqual(response.status_code, 200)
        self.assertEqual(order.payment_status, Order.PaymentStatus.CONFIRMED)
        self.assertEqual(order.wave_transaction_id, 'wave-transaction-test')
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(mail.outbox[0].to, [self.customer.email])

        duplicate_response = self.client.post(reverse('wave_webhook'), data=event_body, content_type='application/json', **headers)
        self.assertEqual(duplicate_response.status_code, 200)
        self.assertEqual(len(mail.outbox), 1)

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

    @override_settings(
        EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend',
        ORDER_NOTIFICATION_EMAIL='owner@example.com',
        WAVE_PAYMENT_LINK_BASE='',
    )
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
        self.assertEqual(len(mail.outbox), 2)
        self.assertEqual(mail.outbox[0].to, [self.customer.email])
        self.assertIn(order.reference, mail.outbox[0].body)
        self.assertIn('5 jours', mail.outbox[0].body)
        self.assertIn('Le lien de paiement Wave vous sera envoyé par e-mail', mail.outbox[0].body)
        self.assertIn(f'http://testserver/commande/{order.reference}/', mail.outbox[0].body)
        self.assertIn('http://testserver/compte/commandes/', mail.outbox[0].body)
        self.assertEqual(mail.outbox[1].to, ['owner@example.com'])
        self.assertIn(order.reference, mail.outbox[1].subject)
        self.assertIn(self.customer.email, mail.outbox[1].body)
        self.assertIn(f'http://testserver/maison/commandes/{order.reference}/', mail.outbox[1].body)
        self.assertNotIn('/maison/commandes/', mail.outbox[0].body)

        order_page = self.client.get(reverse('commande_detail', args=[order.reference]))
        self.assertContains(order_page, 'expédiée sous 5 jours après confirmation du paiement')
        receipt_response = self.client.get(reverse('recu', args=[order.reference]))
        self.assertEqual(receipt_response.status_code, 409)

        self.client.force_login(self.admin)
        confirmation = self.client.post(reverse('admin_paiement_confirmer', args=[order.reference]))
        self.assertRedirects(confirmation, reverse('admin_commande_detail', args=[order.reference]))
        self.assertEqual(len(mail.outbox), 3)
        self.assertIn('Paiement confirmé', mail.outbox[2].subject)
        self.assertIn('commande Yorwani est maintenant confirmée', mail.outbox[2].body)
        self.assertIn('5 jours', mail.outbox[2].body)
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
