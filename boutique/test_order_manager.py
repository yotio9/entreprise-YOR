import csv
import tempfile
from io import StringIO
from datetime import timedelta
from decimal import Decimal
from pathlib import Path

from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase, override_settings
from django.utils import timezone

from boutique.models import Order, OrderChangeBatch, OrderChangeLog, OrderItem, User
from boutique.services.order_manager import (
    OrderManagerError,
    StaleOrderPlan,
    apply_order_change_plan,
    build_order_change_plan,
    export_orders_csv,
    order_detail,
    order_statistics,
    search_orders,
)


class OrderManagerTests(TestCase):
    def setUp(self):
        self.customer = User.objects.create_user(username="order-client", email="order-client@example.test", password="test-pass-123")
        self.order = self.make_order("YOR-ORDER-001")
        OrderItem.objects.create(order=self.order, product_name="Chemise Naya", quantity=2, unit_price=Decimal("12500.00"), size="M")

    def make_order(self, reference, **kwargs):
        defaults = {
            "user": self.customer,
            "customer_name": "Client Test",
            "customer_email": "order-client@example.test",
            "phone": "+221700000000",
            "address": "Rue de test",
            "city": "Dakar",
            "subtotal": Decimal("25000.00"),
            "shipping_fee": Decimal("1000.00"),
            "discount_amount": Decimal("500.00"),
            "payment_method": Order.PaymentMethod.WAVE,
            "payment_status": Order.PaymentStatus.CONFIRMED,
            "status": Order.Status.CONFIRMED,
        }
        defaults.update(kwargs)
        return Order.objects.create(reference=reference, **defaults)

    def plan(self, action="transition", **kwargs):
        return build_order_change_plan(action, {"references": [self.order.reference], **kwargs})

    def test_search_filters_by_reference_customer_status_and_date(self):
        results = search_orders({"query": "client test", "status": Order.Status.CONFIRMED, "date_from": timezone.localdate().isoformat()})
        self.assertEqual([item["reference"] for item in results], [self.order.reference])
        self.assertEqual(search_orders({"query": "missing"}), [])

    def test_detail_includes_item_price_snapshots_and_amounts(self):
        detail = order_detail(self.order.reference.lower())
        self.assertEqual(detail["items"][0]["unit_price"], "12500.00")
        self.assertEqual(detail["items"][0]["line_total"], "25000.00")
        self.assertEqual(detail["amounts"]["total"], "25500.00")

    def test_stats_revenue_excludes_cancelled_and_uses_net_total(self):
        self.make_order("YOR-ORDER-002", status=Order.Status.CANCELLED)
        stats = order_statistics({})
        self.assertEqual(stats["orders"], 2)
        self.assertEqual(stats["cancelled"], 1)
        self.assertEqual(stats["revenue_confirmed_not_cancelled"], "25500.00")

    def test_export_does_not_include_email_or_full_address_and_is_formula_safe(self):
        self.order.customer_name = "=HYPERLINK(\"x\")"
        self.order.save(update_fields=("customer_name",))
        text = export_orders_csv({})
        self.assertNotIn("order-client@example.test", text)
        self.assertNotIn("Rue de test", text)
        self.assertIn("'=HYPERLINK", text)

    def test_preview_does_not_mutate_order_or_write_audit(self):
        plan = self.plan(target_status=Order.Status.PREPARING)
        self.order.refresh_from_db()
        self.assertEqual(self.order.status, Order.Status.CONFIRMED)
        self.assertEqual(OrderChangeBatch.objects.count(), 0)
        self.assertEqual(plan.rows[0].before["status"], Order.Status.CONFIRMED)
        self.assertEqual(plan.rows[0].after["status"], Order.Status.PREPARING)

    def test_prepare_ship_and_deliver_follow_allowed_transitions(self):
        preparing = self.plan(target_status=Order.Status.PREPARING)
        apply_order_change_plan(preparing)
        self.order.refresh_from_db()
        self.assertEqual(self.order.status, Order.Status.PREPARING)
        shipped = self.plan(target_status=Order.Status.SHIPPED, carrier="Yorwani Express", tracking_number="TRACK-01")
        apply_order_change_plan(shipped)
        self.order.refresh_from_db()
        self.assertEqual(self.order.status, Order.Status.SHIPPED)
        self.assertEqual(self.order.tracking_number, "TRACK-01")
        delivered = self.plan(target_status=Order.Status.DELIVERED)
        apply_order_change_plan(delivered)
        self.order.refresh_from_db()
        self.assertEqual(self.order.status, Order.Status.DELIVERED)
        self.assertIsNotNone(self.order.delivered_at)
        self.assertEqual(self.order.loyalty_points_awarded, 25)
        self.customer.refresh_from_db()
        self.assertEqual(self.customer.loyalty_points, 25)

    def test_unpaid_wave_cannot_be_confirmed_prepared_shipped_or_delivered(self):
        self.order.payment_status = Order.PaymentStatus.PENDING
        self.order.status = Order.Status.AWAITING_PAYMENT
        self.order.save(update_fields=("payment_status", "status"))
        for target in (Order.Status.CONFIRMED, Order.Status.PREPARING):
            plan = self.plan(target_status=target)
            self.assertTrue(plan.errors)
            with self.assertRaises(OrderManagerError):
                apply_order_change_plan(plan)
        self.order.refresh_from_db()
        self.assertEqual(self.order.payment_status, Order.PaymentStatus.PENDING)
        self.assertEqual(self.order.status, Order.Status.AWAITING_PAYMENT)

    def test_cash_due_on_delivery_can_prepare_but_cannot_be_marked_delivered(self):
        self.order.payment_method = Order.PaymentMethod.CASH_ON_DELIVERY
        self.order.payment_status = Order.PaymentStatus.DUE_ON_DELIVERY
        self.order.status = Order.Status.AWAITING_PAYMENT
        self.order.save(update_fields=("payment_method", "payment_status", "status"))
        prepare = self.plan(target_status=Order.Status.PREPARING)
        self.assertFalse(prepare.errors)
        apply_order_change_plan(prepare)
        ship = self.plan(target_status=Order.Status.SHIPPED)
        self.assertFalse(ship.errors)
        apply_order_change_plan(ship)
        deliver = self.plan(target_status=Order.Status.DELIVERED)
        self.assertTrue(deliver.errors)

    def test_cancel_requires_reason_and_extra_command_confirmation(self):
        plan = self.plan("cancel", reason="Client requested cancellation")
        self.assertEqual(plan.rows[0].after["cancellation_reason"], "Client requested cancellation")
        with override_settings(DEBUG=True):
            with self.assertRaisesRegex(CommandError, "confirm-cancel"):
                call_command("order_manager", action="cancel", references=[self.order.reference], reason="Client requested cancellation", mode="apply", confirm_digest=plan.fingerprint)
            call_command("order_manager", action="cancel", references=[self.order.reference], reason="Client requested cancellation", mode="apply", confirm_digest=plan.fingerprint, confirm_cancel="ANNULER")
        self.order.refresh_from_db()
        self.assertEqual(self.order.status, Order.Status.CANCELLED)
        self.assertEqual(self.order.payment_status, Order.PaymentStatus.CONFIRMED)
        self.assertEqual(OrderChangeLog.objects.get().after["cancellation_reason"], "Client requested cancellation")

    def test_cancel_rejects_missing_reason_and_shipped_order(self):
        self.assertTrue(self.plan("cancel").errors)
        self.order.status = Order.Status.SHIPPED
        self.order.save(update_fields=("status",))
        plan = self.plan("cancel", reason="No longer wanted")
        self.assertTrue(plan.errors)

    def test_transition_to_cancelled_requires_cancel_action(self):
        with self.assertRaisesRegex(OrderManagerError, "action cancel"):
            self.plan(target_status=Order.Status.CANCELLED)

    def test_stale_order_or_order_item_snapshot_rejects_apply(self):
        plan = self.plan(target_status=Order.Status.PREPARING)
        OrderItem.objects.filter(order=self.order).update(unit_price=Decimal("12750.00"))
        with self.assertRaises(StaleOrderPlan):
            apply_order_change_plan(plan)
        self.assertEqual(OrderChangeBatch.objects.count(), 0)

    def test_duplicate_and_missing_csv_references_are_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "orders.csv"
            path.write_text(f"reference\n{self.order.reference}\n{self.order.reference}\n", encoding="utf-8")
            with self.assertRaisesRegex(OrderManagerError, "dupliquees"):
                build_order_change_plan("transition", {"csv": path, "target_status": Order.Status.PREPARING})
            path.write_text("reference\nYOR-MISSING\n", encoding="utf-8")
            with self.assertRaisesRegex(OrderManagerError, "introuvables"):
                build_order_change_plan("transition", {"csv": path, "target_status": Order.Status.PREPARING})

    def test_csv_selection_and_retry_are_idempotent(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "orders.csv"
            path.write_text(f"reference\n{self.order.reference}\n", encoding="utf-8")
            plan = build_order_change_plan("transition", {"csv": path, "target_status": Order.Status.PREPARING})
        first = apply_order_change_plan(plan)
        second = apply_order_change_plan(plan)
        self.assertEqual(first["status"], "applied")
        self.assertTrue(second["already_applied"])
        self.assertEqual(OrderChangeBatch.objects.count(), 1)
        self.assertEqual(OrderChangeLog.objects.count(), 1)

    def test_csv_export_writes_one_bom_and_refuses_to_overwrite(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "orders.csv"
            with override_settings(DEBUG=True):
                call_command("order_manager", action="export", output=str(path), stdout=StringIO())
                self.assertEqual(path.read_bytes().count(b"\xef\xbb\xbf"), 1)
                with self.assertRaisesRegex(CommandError, "Export impossible"):
                    call_command("order_manager", action="export", output=str(path), stdout=StringIO())

    def test_order_and_item_cannot_be_deleted_after_audit(self):
        apply_order_change_plan(self.plan(target_status=Order.Status.PREPARING))
        with self.assertRaises(Exception):
            self.order.delete()
        self.assertTrue(OrderItem.objects.filter(order=self.order).exists())

    def test_cli_stale_digest_is_rejected(self):
        plan = self.plan(target_status=Order.Status.PREPARING)
        Order.objects.filter(pk=self.order.pk).update(tracking_number="updated")
        with override_settings(DEBUG=True):
            with self.assertRaisesRegex(CommandError, "modifiees depuis PREVIEW"):
                call_command("order_manager", action="transition", references=[self.order.reference], status=Order.Status.PREPARING, mode="apply", confirm_digest=plan.fingerprint)
        self.assertEqual(OrderChangeBatch.objects.count(), 0)
