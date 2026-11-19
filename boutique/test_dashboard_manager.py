import csv
import tempfile
from datetime import datetime, timedelta
from decimal import Decimal
from io import StringIO
from pathlib import Path

from django.core.management import call_command
from django.test import TestCase, override_settings
from django.utils import timezone

from boutique.models import Category, Order, OrderItem, Product, User
from boutique.services.dashboard_manager import (
    DashboardManagerError,
    collection_report,
    compare_periods,
    csv_export,
    inventory_report,
    overview,
    period_bounds,
    report,
    revenue_summary,
    sales_analysis,
    top_collections,
    top_products,
)


class DashboardManagerTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="dash-client", email="dash@example.test", password="test-pass")
        self.category = Category.objects.create(name="Essentiels")
        self.other_category = Category.objects.create(name="Accessoires")
        self.product = self.make_product("Robe Naya", self.category, price="1000.00", stock=4)
        self.product_b = self.make_product("Sac Malia", self.other_category, price="2500.00", stock=0)

    def make_product(self, name, category, price="1000", stock=1, active=True):
        return Product.objects.create(category=category, name=name, description="Produit test", price=Decimal(price), stock=stock, is_active=active)

    def make_order(self, reference, created_at=None, status=Order.Status.CONFIRMED, payment_status=Order.PaymentStatus.CONFIRMED,
                   product=None, quantity=1, unit_price="1000.00", shipping="100.00", discount="50.00"):
        order = Order.objects.create(
            reference=reference,
            user=self.user,
            status=status,
            payment_status=payment_status,
            payment_method=Order.PaymentMethod.WAVE,
            customer_name="Dash Client",
            customer_email=self.user.email,
            phone="7700000000",
            address="Private address",
            city="Dakar",
            subtotal=Decimal(unit_price) * quantity,
            shipping_fee=Decimal(shipping),
            discount_amount=Decimal(discount),
        )
        if created_at:
            Order.objects.filter(pk=order.pk).update(created_at=created_at)
            order.refresh_from_db()
        OrderItem.objects.create(order=order, product=product or self.product, product_name=(product or self.product).name, quantity=quantity, unit_price=Decimal(unit_price))
        return order

    def test_overview_reports_lifetime_daily_week_month_counts_and_inventory(self):
        now = timezone.localtime()
        self.make_order("YOR-DASH-001", created_at=now)
        self.make_order("YOR-DASH-002", created_at=now, status=Order.Status.SHIPPED)
        self.make_order("YOR-DASH-003", created_at=now, status=Order.Status.CANCELLED)
        User.objects.create_user(username="new-client", email="new@example.test", password="test-pass")
        admin = User.objects.create_superuser(username="admin", email="admin@example.test", password="admin-pass")
        self.assertTrue(admin.is_superuser)

        result = overview("month")
        self.assertEqual(result["revenue_realized_paid_non_cancelled"], "2100.00")
        self.assertEqual(result["revenue_today"], "2100.00")
        self.assertEqual(result["orders_total"], 3)
        self.assertEqual(result["orders_by_status"][Order.Status.SHIPPED], 1)
        self.assertEqual(result["orders_by_status"][Order.Status.CANCELLED], 1)
        self.assertEqual(result["customers_total"], 2)
        self.assertEqual(result["new_customers_in_period"], 2)
        self.assertEqual(result["active_products"], 2)
        self.assertEqual(result["active_products_out_of_stock"], 1)
        self.assertEqual(result["active_inventory_retail_value_estimate"], "4000.00")
        self.assertFalse(result["margins_calculated"])

    def test_revenue_separates_paid_pending_and_cancelled_payments(self):
        self.make_order("YOR-REV-PAID", product=self.product, quantity=2, unit_price="1000.00")
        self.make_order("YOR-REV-PENDING", status=Order.Status.AWAITING_PAYMENT, payment_status=Order.PaymentStatus.PENDING, product=self.product_b, quantity=1, unit_price="2500.00")
        self.make_order("YOR-REV-COD", status=Order.Status.PREPARING, payment_status=Order.PaymentStatus.DUE_ON_DELIVERY, product=self.product, quantity=1)
        self.make_order("YOR-REV-CANCEL", status=Order.Status.CANCELLED, payment_status=Order.PaymentStatus.CONFIRMED, product=self.product_b, unit_price="2500.00")
        result = revenue_summary()
        self.assertEqual(result["sales_realized_paid_non_cancelled"], "2050.00")
        self.assertEqual(result["sales_orders_paid_non_cancelled"], 1)
        self.assertEqual(result["amount_awaiting_payment_non_cancelled"], "3600.00")
        self.assertEqual(result["orders_awaiting_payment_non_cancelled"], 2)
        self.assertEqual(result["cancelled_orders_with_confirmed_payment_not_refund_adjusted"], "2550.00")
        self.assertEqual(result["payment_status_distribution"][Order.PaymentStatus.DUE_ON_DELIVERY]["orders_non_cancelled"], 1)
        self.assertFalse(result["refund_status_available"])

    def test_sales_uses_orderitem_snapshots_and_does_not_double_count_orders(self):
        created = timezone.localtime()
        order = self.make_order("YOR-SALES-001", created_at=created, product=self.product, quantity=2, unit_price="700.00")
        OrderItem.objects.create(order=order, product=self.product_b, product_name="Sac ancien", quantity=3, unit_price=Decimal("400.00"))
        Order.objects.filter(pk=order.pk).update(subtotal=Decimal("2600.00"))
        self.product.price = Decimal("5000.00")
        self.product.save(update_fields=("price", "updated_at"))
        result = sales_analysis("day", granularity="day")
        self.assertEqual(result["paid_non_cancelled_orders"], 1)
        self.assertEqual(result["items_sold_paid_non_cancelled"], 5)
        self.assertEqual(result["average_basket"], "2650.00")
        self.assertEqual(result["order_item_snapshot_check"]["mismatched_or_missing_orders"], 0)
        self.assertEqual(result["series"][0]["orders"], 1)
        best = top_products(sort="best")
        robe = next(row for row in best["products"] if row["product_id"] == self.product.pk)
        self.assertEqual(robe["units_sold"], 2)
        self.assertEqual(robe["line_revenue_at_historical_prices"], "1400.00")

    def test_sales_report_flags_order_item_snapshot_mismatch(self):
        order = self.make_order("YOR-MISMATCH", quantity=2, unit_price="1000.00")
        Order.objects.filter(pk=order.pk).update(subtotal=Decimal("1500.00"))
        result = sales_analysis("all")
        self.assertEqual(result["order_item_snapshot_check"]["mismatched_or_missing_orders"], 1)
        self.assertEqual(result["order_item_snapshot_check"]["sample_references"][0]["reference"], "YOR-MISMATCH")

    def test_top_products_excludes_cancelled_and_unpaid_and_least_includes_zero_sales(self):
        self.make_order("YOR-PROD-PAID", product=self.product, quantity=3)
        self.make_order("YOR-PROD-PENDING", status=Order.Status.AWAITING_PAYMENT, payment_status=Order.PaymentStatus.PENDING, product=self.product_b, quantity=10, unit_price="2500")
        self.make_order("YOR-PROD-CANCEL", status=Order.Status.CANCELLED, product=self.product_b, quantity=5, unit_price="2500")
        best = top_products(sort="best")
        robe = next(row for row in best["products"] if row["product_id"] == self.product.pk)
        self.assertEqual(robe["units_sold"], 3)
        least = top_products(sort="least")
        bag = next(row for row in least["products"] if row["product_id"] == self.product_b.pk)
        self.assertEqual(bag["units_sold"], 0)

    def test_collection_report_uses_current_category_and_reports_limitation(self):
        self.make_order("YOR-COLL-001", product=self.product, quantity=2)
        result = top_collections()
        self.assertEqual(result["collections"][0]["collection"], "Essentiels")
        self.assertIn("actuelle", result["attribution_note"])
        detail = collection_report("Accessoires")
        self.assertEqual(detail["collections"][0]["units_sold"], 0)

    def test_inventory_value_is_retail_estimate_not_margin_or_cost(self):
        report_data = inventory_report()
        self.assertEqual(report_data["active_products"], 2)
        self.assertEqual(report_data["units_in_stock"], 4)
        self.assertEqual(report_data["out_of_stock_products"], 1)
        self.assertEqual(report_data["inventory_retail_value_estimate"], "4000.00")
        self.assertFalse(report_data["purchase_cost_available"])
        self.assertFalse(report_data["margins_available"])

    def test_custom_period_comparison_computes_variations(self):
        today = timezone.localdate()
        current_day = timezone.make_aware(datetime.combine(today, datetime.min.time())) + timedelta(hours=10)
        previous_day = current_day - timedelta(days=1)
        self.make_order("YOR-CMP-CURRENT", created_at=current_day, quantity=2)
        self.make_order("YOR-CMP-PREVIOUS", created_at=previous_day, quantity=1)
        result = compare_periods("custom", current_day.date().isoformat(), current_day.date().isoformat(), previous_day.date().isoformat(), previous_day.date().isoformat())
        self.assertEqual(result["current"]["items_sold"], 2)
        self.assertEqual(result["previous"]["items_sold"], 1)
        self.assertEqual(result["variation_percent"]["items_sold"], "100.00")

    def test_month_compare_uses_previous_calendar_month(self):
        now = timezone.localtime()
        this_month_start = now.date().replace(day=1)
        last_month_day = this_month_start - timedelta(days=1)
        self.make_order("YOR-MONTH-CURRENT", created_at=timezone.make_aware(datetime.combine(this_month_start, datetime.min.time())) + timedelta(hours=1))
        self.make_order("YOR-MONTH-PREV", created_at=timezone.make_aware(datetime.combine(last_month_day, datetime.min.time())) + timedelta(hours=1), quantity=2)
        result = compare_periods("month")
        self.assertEqual(result["previous_period"]["from"], last_month_day.replace(day=1).isoformat())
        self.assertEqual(result["previous"]["items_sold"], 2)

    def test_period_boundaries_use_local_calendar(self):
        fixed_now = timezone.make_aware(datetime(2026, 11, 18, 13, 0))
        start, end, _, _ = period_bounds("week", now=fixed_now)
        self.assertEqual(start.weekday(), 0)
        self.assertEqual((end - start).days, 7)
        month_start, month_end, _, _ = period_bounds("month", now=fixed_now)
        self.assertEqual(month_start.day, 1)
        self.assertEqual(month_end.day, 1)

    def test_reports_daily_weekly_monthly_and_date_validation(self):
        for report_type, expected in (("daily", "day"), ("weekly", "week"), ("monthly", "month")):
            data = report(report_type)
            self.assertEqual(data["granularity"], expected)
        with self.assertRaises(DashboardManagerError):
            sales_analysis("custom", date_from="2026-11-20", date_to="2026-11-01")

    def test_csv_export_is_read_only_and_structured(self):
        before = (Order.objects.count(), OrderItem.objects.count(), Product.objects.count())
        csv_text = csv_export({"action": "test", "series": [{"date": "2026-01-01", "revenue": "10.00"}]})
        self.assertTrue(csv_text.startswith("\ufeff"))
        rows = list(csv.DictReader(StringIO(csv_text.lstrip("\ufeff"))))
        self.assertTrue(any(row["metric"] == "series[0]" for row in rows))
        self.assertEqual(before, (Order.objects.count(), OrderItem.objects.count(), Product.objects.count()))

    def test_management_command_exports_csv_without_overwriting(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "inventory.csv"
            with override_settings(DEBUG=True):
                call_command("dashboard_manager", action="inventory", format="csv", output=str(path), stdout=StringIO())
                self.assertEqual(path.read_bytes().count(b"\xef\xbb\xbf"), 1)
                with self.assertRaises(Exception):
                    call_command("dashboard_manager", action="inventory", format="csv", output=str(path), stdout=StringIO())

    def test_command_period_and_report_options(self):
        with override_settings(DEBUG=True):
            output = StringIO()
            call_command("dashboard_manager", action="sales", period="month", format="json", stdout=output)
            self.assertIn("revenue_realized_paid_non_cancelled", output.getvalue())
            output = StringIO()
            call_command("dashboard_manager", action="report", report_type="product", product_id=self.product.pk, stdout=output)
            self.assertIn("Robe Naya", output.getvalue())

    def test_stats_and_reports_do_not_mutate_database(self):
        self.make_order("YOR-READONLY-001", quantity=2)
        before = {
            "orders": Order.objects.count(),
            "items": OrderItem.objects.count(),
            "customers": User.objects.count(),
            "products": Product.objects.count(),
        }
        overview()
        sales_analysis("all")
        revenue_summary()
        top_products()
        top_collections()
        inventory_report()
        compare_periods("month")
        after = {
            "orders": Order.objects.count(),
            "items": OrderItem.objects.count(),
            "customers": User.objects.count(),
            "products": Product.objects.count(),
        }
        self.assertEqual(before, after)
