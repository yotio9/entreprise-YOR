import csv
import tempfile
from decimal import Decimal
from io import StringIO
from pathlib import Path

from django.contrib.auth import authenticate
from django.core.management import call_command
from django.core.management.base import CommandError
from django.db.models.deletion import ProtectedError
from django.test import TestCase, override_settings
from django.utils import timezone

from boutique.models import CustomerChangeBatch, CustomerChangeLog, Order, OrderItem, User
from boutique.services.customer_manager import (
    CustomerManagerError,
    StaleCustomerPlan,
    apply_customer_change_plan,
    build_customer_change_plan,
    customer_detail,
    customer_order_history,
    customer_statistics,
    export_customer_orders_csv,
    export_customers_csv,
    search_customers,
    top_customers,
)


class CustomerManagerTests(TestCase):
    def setUp(self):
        self.customer = User.objects.create_user(
            username="client@example.test", email="client@example.test", first_name="Awa", last_name="Diallo",
            phone="7700000000", password="test-password-123",
        )
        self.order = self.make_order("YOR-CUSTOMER-001")
        OrderItem.objects.create(order=self.order, product_name="Robe Naya", quantity=2, unit_price=Decimal("10000.00"))

    def make_order(self, reference, **kwargs):
        defaults = {
            "user": self.customer,
            "customer_name": "Awa Diallo",
            "customer_email": self.customer.email,
            "phone": self.customer.phone,
            "address": "Adresse privee non exportee",
            "city": "Dakar",
            "subtotal": Decimal("20000.00"),
            "shipping_fee": Decimal("1000.00"),
            "discount_amount": Decimal("500.00"),
            "payment_method": Order.PaymentMethod.WAVE,
            "payment_status": Order.PaymentStatus.CONFIRMED,
            "status": Order.Status.DELIVERED,
        }
        defaults.update(kwargs)
        return Order.objects.create(reference=reference, **defaults)

    def plan(self, action="suspend", **kwargs):
        return build_customer_change_plan(action, {"customer_ids": [self.customer.pk], "emails": None, "csv": None, **kwargs})

    def test_search_by_name_email_phone_and_id(self):
        for query in ("Awa", "client@example.test", "7700000000", str(self.customer.pk)):
            results = search_customers({"query": query})
            self.assertEqual([row["id"] for row in results], [self.customer.pk])

    def test_detail_exposes_account_and_purchase_summary_but_no_secrets(self):
        detail = customer_detail(customer_id=self.customer.pk)
        self.assertEqual(detail["order_count"], 1)
        self.assertEqual(detail["paid_purchase_count"], 1)
        self.assertEqual(detail["total_spent"], "20500.00")
        self.assertNotIn("password", detail)
        self.assertNotIn("last_login_token", detail)

    def test_history_contains_all_orders_and_can_filter_by_status_and_period(self):
        self.make_order("YOR-CUSTOMER-002", status=Order.Status.CANCELLED, payment_status=Order.PaymentStatus.CONFIRMED)
        all_orders = customer_order_history(customer_id=self.customer.pk)
        self.assertEqual(len(all_orders["orders"]), 2)
        delivered = customer_order_history(customer_id=self.customer.pk, order_status=Order.Status.DELIVERED, date_from=timezone.localdate().isoformat())
        self.assertEqual([row["reference"] for row in delivered["orders"]], [self.order.reference])
        self.assertEqual(delivered["orders"][0]["items"][0]["unit_price"], "10000.00")

    def test_statistics_and_activity_buckets(self):
        inactive = User.objects.create_user(username="inactive", email="inactive@example.test", password="test-password")
        inactive.is_active = False
        inactive.save(update_fields=("is_active",))
        no_order = User.objects.create_user(username="no-order", email="no-order@example.test", password="test-password")
        stats = customer_statistics(timezone.localdate().isoformat(), timezone.localdate().isoformat())
        self.assertEqual(stats["total"], 3)
        self.assertEqual(stats["new_in_period"], 3)
        self.assertEqual(stats["active"], 2)
        self.assertEqual(stats["suspended"], 1)
        self.assertEqual(stats["with_orders"], 1)
        self.assertEqual(stats["activity"]["one_paid_order"], 1)
        self.assertEqual(stats["activity"]["no_paid_orders"], 2)
        self.assertTrue(no_order.pk)

    def test_top_customers_rank_by_confirmed_net_spend_and_skip_cancelled(self):
        self.make_order("YOR-CUSTOMER-002", status=Order.Status.CANCELLED, payment_status=Order.PaymentStatus.CONFIRMED, subtotal=Decimal("90000.00"))
        self.make_order("YOR-CUSTOMER-003", payment_status=Order.PaymentStatus.PENDING, subtotal=Decimal("80000.00"))
        self.assertEqual(top_customers()[0]["total_spent"], "20500.00")

    def test_customer_csv_has_contact_and_purchase_summary_but_no_password_or_address(self):
        text = export_customers_csv({})
        self.assertIn("client@example.test", text)
        self.assertIn("7700000000", text)
        self.assertIn("20500.00", text)
        self.assertNotIn("test-password", text)
        self.assertNotIn("Adresse privee", text)

    def test_order_history_csv_is_scoped_to_one_customer_and_filterable(self):
        text = export_customer_orders_csv(customer_id=self.customer.pk, order_status=Order.Status.DELIVERED)
        self.assertIn(self.order.reference, text)
        self.assertNotIn("Adresse privee", text)
        self.assertNotIn("client@example.test", text)

    def test_preview_is_read_only_and_suspend_reason_is_required(self):
        plan = self.plan(reason="Repeated abusive messages")
        self.assertTrue(plan.has_changes)
        self.assertEqual(plan.rows[0].before["is_active"], True)
        self.assertEqual(plan.rows[0].after["is_active"], False)
        self.customer.refresh_from_db()
        self.assertTrue(self.customer.is_active)
        self.assertEqual(CustomerChangeBatch.objects.count(), 0)
        invalid = self.plan(reason="")
        self.assertTrue(invalid.errors)
        with self.assertRaises(CustomerManagerError):
            apply_customer_change_plan(invalid)

    def test_suspend_blocks_login_and_reactivation_restores_login(self):
        self.assertIsNotNone(authenticate(username=self.customer.username, password="test-password-123"))
        plan = self.plan(reason="Repeated abusive messages")
        apply_customer_change_plan(plan)
        self.customer.refresh_from_db()
        self.assertFalse(self.customer.is_active)
        self.assertIsNone(authenticate(username=self.customer.username, password="test-password-123"))
        reactivate = self.plan("reactivate")
        apply_customer_change_plan(reactivate)
        self.customer.refresh_from_db()
        self.assertTrue(self.customer.is_active)
        self.assertIsNotNone(authenticate(username=self.customer.username, password="test-password-123"))
        self.assertEqual(CustomerChangeLog.objects.count(), 2)

    def test_staff_and_superuser_accounts_are_never_suspendable(self):
        admin = User.objects.create_superuser(username="superuser", email="admin@example.test", password="admin-pass")
        plan = build_customer_change_plan("suspend", {"customer_ids": [admin.pk], "reason": "Policy violation"})
        self.assertTrue(plan.errors)
        with self.assertRaises(CustomerManagerError):
            apply_customer_change_plan(plan)
        staff = User.objects.create_user(username="staff", email="staff@example.test", password="staff-pass", is_staff=True)
        plan = build_customer_change_plan("suspend", {"customer_ids": [staff.pk], "reason": "Policy violation"})
        self.assertTrue(plan.errors)

    def test_command_requires_digest_and_explicit_suspension_confirmation(self):
        plan = self.plan(reason="Repeated abusive messages")
        with override_settings(DEBUG=True):
            with self.assertRaisesRegex(CommandError, "confirm-digest"):
                call_command("customer_manager", action="suspend", customer_ids=[self.customer.pk], reason="Repeated abusive messages", mode="apply")
            with self.assertRaisesRegex(CommandError, "confirm-suspension"):
                call_command("customer_manager", action="suspend", customer_ids=[self.customer.pk], reason="Repeated abusive messages", mode="apply", confirm_digest=plan.fingerprint)
            call_command("customer_manager", action="suspend", customer_ids=[self.customer.pk], reason="Repeated abusive messages", mode="apply", confirm_digest=plan.fingerprint, confirm_suspension="SUSPENDRE")
        self.customer.refresh_from_db()
        self.assertFalse(self.customer.is_active)

    def test_stale_identity_or_permission_change_rejects_apply(self):
        plan = self.plan(reason="Repeated abusive messages")
        User.objects.filter(pk=self.customer.pk).update(is_staff=True)
        with self.assertRaises(StaleCustomerPlan):
            apply_customer_change_plan(plan)
        self.assertTrue(User.objects.get(pk=self.customer.pk).is_active)
        self.assertEqual(CustomerChangeBatch.objects.count(), 0)

    def test_bulk_csv_apply_is_audited_and_retry_is_idempotent(self):
        other = User.objects.create_user(username="other", email="other@example.test", password="test-password")
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "customers.csv"
            path.write_text(f"email;\n{self.customer.email};\n{other.email};\n", encoding="utf-8")
            plan = build_customer_change_plan("suspend", {"csv": path, "reason": "Requested account closure"})
        self.assertEqual(len(plan.rows), 2)
        first = apply_customer_change_plan(plan)
        retry = apply_customer_change_plan(plan)
        self.assertEqual(first["changed"], 2)
        self.assertTrue(retry["already_applied"])
        self.assertEqual(CustomerChangeLog.objects.count(), 2)

    def test_duplicate_and_missing_customer_csv_are_preview_errors(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "customers.csv"
            path.write_text(f"email\n{self.customer.email}\n{self.customer.email}\n", encoding="utf-8")
            duplicate = build_customer_change_plan("suspend", {"csv": path, "reason": "Requested account closure"})
            self.assertTrue(duplicate.errors)
            path.write_text("id\n999999\n", encoding="utf-8")
            missing = build_customer_change_plan("reactivate", {"csv": path})
            self.assertTrue(missing.errors)

    def test_audit_log_protects_customer_deletion(self):
        apply_customer_change_plan(self.plan(reason="Repeated abusive messages"))
        with self.assertRaises(ProtectedError):
            self.customer.delete()

    def test_csv_export_creates_file_once_without_overwrite(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "customers.csv"
            with override_settings(DEBUG=True):
                call_command("customer_manager", action="export", output=str(output), stdout=StringIO())
                self.assertEqual(output.read_bytes().count(b"\xef\xbb\xbf"), 1)
                with self.assertRaisesRegex(CommandError, "Export impossible"):
                    call_command("customer_manager", action="export", output=str(output), stdout=StringIO())
