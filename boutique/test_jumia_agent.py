from decimal import Decimal
from io import StringIO
from unittest.mock import patch

from django.core.management import call_command
from django.test import TestCase, override_settings

from .agents.jumia import assess_listing, parse_price, validate_jumia_url
from .agents.worker import process_task
from .models import Category, JumiaOrderAttempt, JumiaOrderTask, Order, OrderItem, Product, User


class JumiaAgentSafetyTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username="buyer@example.test",
            email="buyer@example.test",
            password="safe-test-password",
        )
        self.category = Category.objects.create(name="Mode")
        self.product = Product.objects.create(
            category=self.category,
            name="Robe Awa",
            description="Robe de d?monstration",
            price=Decimal("25000.00"),
            stock=3,
            color="Bleu",
            source_url="https://www.jumia.ci/robe-awa",
            source_price_limit=Decimal("15000.00"),
        )
        self.order = Order.objects.create(
            user=self.user,
            status=Order.Status.CONFIRMED,
            payment_status=Order.PaymentStatus.CONFIRMED,
            payment_method=Order.PaymentMethod.WAVE,
            customer_name="Client Test",
            customer_email=self.user.email,
            phone="0700000000",
            address="Adresse test",
            city="Abidjan",
            subtotal=Decimal("25000.00"),
        )
        self.item = OrderItem.objects.create(
            order=self.order,
            product=self.product,
            product_name=self.product.name,
            size="M",
            quantity=1,
            unit_price=self.product.price,
        )

    def test_only_https_jumia_urls_are_allowed(self):
        self.assertTrue(validate_jumia_url("https://www.jumia.ci/item"))
        self.assertTrue(validate_jumia_url("https://www.jumia.com.ng/item"))
        self.assertFalse(validate_jumia_url("http://www.jumia.ci/item"))
        self.assertFalse(validate_jumia_url("https://jumia.ci.attacker.example/item"))
        self.assertFalse(validate_jumia_url("https://user:pass@www.jumia.ci/item"))
        self.assertFalse(validate_jumia_url("https://127.0.0.1/item"))

    def test_price_parser_handles_localized_thousands(self):
        self.assertEqual(parse_price("12 500 FCFA"), Decimal("12500"))
        self.assertEqual(parse_price("12,500 XOF"), Decimal("12500"))
        self.assertIsNone(parse_price("Prix indisponible"))

    def test_assessment_requires_product_variant_stock_currency_and_cap(self):
        task = JumiaOrderTask(
            expected_product_name="Robe Awa",
            expected_size="M",
            expected_color="Bleu",
            price_limit=Decimal("15000"),
        )
        listing = {
            "title": "Robe Awa Bleu",
            "price": "12000",
            "currency": "XOF",
            "availability": "available",
            "variant_labels": ["Taille M", "Bleu"],
            "challenge": False,
        }
        with override_settings(JUMIA_EXPECTED_CURRENCY="XOF"):
            self.assertTrue(assess_listing(task, listing)["prepared"])
            listing["price"] = "16000"
            result = assess_listing(task, listing)
            self.assertFalse(result["prepared"])
            self.assertIn("price_limit_exceeded", result["issues"])

    @override_settings(JUMIA_AGENT_MAX_ATTEMPTS=3)
    def test_queue_requires_paid_confirmed_order_and_is_idempotent(self):
        output = StringIO()
        self.order.payment_status = Order.PaymentStatus.PENDING
        self.order.save(update_fields=("payment_status",))
        call_command("queue_jumia_orders", "--order-ref", self.order.reference, stdout=output)
        self.assertEqual(JumiaOrderTask.objects.count(), 0)

        self.order.payment_status = Order.PaymentStatus.CONFIRMED
        self.order.save(update_fields=("payment_status",))
        call_command("queue_jumia_orders", "--order-ref", self.order.reference, stdout=output)
        call_command("queue_jumia_orders", "--order-ref", self.order.reference, stdout=output)
        task = JumiaOrderTask.objects.get(order_item=self.item)
        self.assertEqual(JumiaOrderTask.objects.count(), 1)
        self.assertTrue(task.simulation)
        self.assertEqual(task.price_limit, Decimal("15000.00"))

    def test_worker_prepares_read_only_and_requires_human_before_checkout(self):
        task = JumiaOrderTask.objects.create(
            order_item=self.item,
            source_url=self.product.source_url,
            expected_product_name=self.product.name,
            expected_color="Bleu",
            expected_size="M",
            quantity=1,
            price_limit=Decimal("15000.00"),
            status=JumiaOrderTask.Status.RUNNING,
            attempt_count=1,
            claimed_at=__import__("django.utils.timezone", fromlist=["now"]).now(),
        )
        attempt = JumiaOrderAttempt.objects.create(task=task, number=1)
        listing = {
            "title": "Robe Awa Bleu",
            "price": "12000",
            "currency": "XOF",
            "availability": "available",
            "variant_labels": ["Taille M", "Bleu"],
            "challenge": False,
            "observed_url_host": "www.jumia.ci",
        }
        status = process_task(
            task,
            attempt,
            inspector=lambda _task: listing,
            llm_analyzer=lambda _task, _listing: {"status": "not_configured"},
        )
        task.refresh_from_db()
        attempt.refresh_from_db()
        self.assertEqual(status, JumiaOrderTask.Status.PREPARED)
        self.assertEqual(task.status, JumiaOrderTask.Status.PREPARED)
        self.assertIn("add_to_cart", task.result_data["actions_not_taken"])
        self.assertIn("checkout", task.result_data["actions_not_taken"])
        self.assertEqual(attempt.status, JumiaOrderAttempt.Status.PREPARED)

    def test_unverified_supplier_page_stops_for_human(self):
        task = JumiaOrderTask.objects.create(
            order_item=self.item,
            source_url=self.product.source_url,
            expected_product_name=self.product.name,
            expected_color="Bleu",
            expected_size="M",
            quantity=1,
            price_limit=None,
            status=JumiaOrderTask.Status.RUNNING,
            attempt_count=1,
        )
        attempt = JumiaOrderAttempt.objects.create(task=task, number=1)
        listing = {
            "title": "Robe Awa",
            "price": "12000",
            "currency": "XOF",
            "availability": "available",
            "variant_labels": ["Taille M", "Bleu"],
            "challenge": False,
        }
        process_task(task, attempt, inspector=lambda _task: listing, llm_analyzer=lambda *_: {"status": "not_configured"})
        task.refresh_from_db()
        self.assertEqual(task.status, JumiaOrderTask.Status.NEEDS_HUMAN)
        self.assertIn("price_limit_missing", task.result_data["assessment"]["issues"])
