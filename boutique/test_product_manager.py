import csv
import tempfile
from decimal import Decimal
from pathlib import Path
from unittest.mock import patch

from django.core.files.storage import FileSystemStorage
from django.core.management import call_command
from django.core.management.base import CommandError
from django.db import IntegrityError
from django.test import TestCase, override_settings
from PIL import Image

from boutique.models import (
	Category,
	Order,
	OrderItem,
	Product,
	ProductChangeBatch,
	ProductChangeLog,
	User,
)
from boutique.services.product_manager import (
	ProductManagerError,
	StaleProductPlan,
	apply_product_change_plan,
	build_product_change_plan,
	build_search_results,
)


class ProductManagerTests(TestCase):
	def setUp(self):
		self.tempdir = tempfile.TemporaryDirectory()
		self.addCleanup(self.tempdir.cleanup)
		self.root = Path(self.tempdir.name)
		self.image_dir = self.root / "images"
		self.image_dir.mkdir()
		self.storage_dir = self.root / "storage"
		self.storage_dir.mkdir()
		storage = FileSystemStorage(location=self.storage_dir)
		self.storage_patch = patch("boutique.services.product_manager.default_storage", storage)
		self.storage_patch.start()
		self.addCleanup(self.storage_patch.stop)
		self.collection = Category.objects.create(name="Essentiels")
		self.other_collection = Category.objects.create(name="Accessoires")
		self.product = Product.objects.create(
			category=self.collection,
			sku="YOR-001",
			name="Chemise Naya",
			description="Chemise de coton.",
			price=Decimal("10000.00"),
			stock=8,
		)
		self.options = {
			"product_id": self.product.pk,
			"sku": None,
			"collection": None,
			"target_collection": None,
			"csv": None,
			"images": None,
			"price": None,
			"percent": None,
			"stock": None,
			"name": None,
			"description": None,
			"image_filename": None,
		}

	def write_csv(self, rows, headers=None, delimiter=","):
		headers = headers or list(rows[0])
		path = self.root / "changes.csv"
		with path.open("w", encoding="utf-8-sig", newline="") as handle:
			writer = csv.DictWriter(handle, fieldnames=headers, delimiter=delimiter)
			writer.writeheader()
			writer.writerows(rows)
		return path

	def create_image(self, name="main.png", color="blue"):
		path = self.image_dir / name
		Image.new("RGB", (12, 12), color=color).save(path, format="PNG")
		return path

	def test_preview_has_before_after_and_writes_nothing(self):
		options = {**self.options, "price": "12000.00"}
		plan = build_product_change_plan("set-price", options)

		self.assertEqual(plan.rows[0].changes["price"], {"before": "10000.00", "after": "12000.00"})
		self.assertEqual(self.product.price, Decimal("10000.00"))
		self.assertEqual(ProductChangeBatch.objects.count(), 0)
		self.assertEqual(ProductChangeLog.objects.count(), 0)

	def test_fixed_price_apply_is_audited_and_preserves_order_snapshot(self):
		customer = User.objects.create_user(username="customer", email="customer@example.test", password="safe-test-pass")
		order = Order.objects.create(
			user=customer,
			customer_name="Client",
			customer_email=customer.email,
			phone="0000000000",
			address="Rue test",
			city="Dakar",
		)
		item = OrderItem.objects.create(
			order=order,
			product=self.product,
			product_name=self.product.name,
			quantity=1,
			unit_price=Decimal("9000.00"),
		)
		plan = build_product_change_plan("set-price", {**self.options, "price": "12000"})
		result = apply_product_change_plan(plan)

		self.product.refresh_from_db()
		item.refresh_from_db()
		self.assertEqual(self.product.price, Decimal("12000.00"))
		self.assertEqual(item.unit_price, Decimal("9000.00"))
		self.assertEqual(result["status"], "applied")
		self.assertEqual(ProductChangeLog.objects.get().before["price"], "10000.00")
		self.assertEqual(ProductChangeLog.objects.get().after["price"], "12000.00")

	def test_collection_price_increase_and_decrease(self):
		options = {**self.options, "product_id": None, "collection": "Essentiels", "percent": "10"}
		increase = build_product_change_plan("set-price", options)
		apply_product_change_plan(increase)
		self.product.refresh_from_db()
		self.assertEqual(self.product.price, Decimal("11000.00"))

		decrease = build_product_change_plan("set-price", {**options, "percent": "-20"})
		apply_product_change_plan(decrease)
		self.product.refresh_from_db()
		self.assertEqual(self.product.price, Decimal("8800.00"))

	def test_stock_set_from_csv(self):
		path = self.write_csv([{"sku": "YOR-001", "stock": "3"}])
		plan = build_product_change_plan("set-stock", {"csv": path, "images": None})
		result = apply_product_change_plan(plan)
		self.product.refresh_from_db()
		self.assertEqual(self.product.stock, 3)
		self.assertEqual(result["changed"], 1)

	def test_csv_updates_name_description_collection_and_main_image(self):
		self.create_image("hero.png")
		path = self.write_csv([{
			"sku": "YOR-001",
			"name": "Chemise Malia",
			"description": "Nouvelle description.",
			"collection": "Accessoires",
			"image_filename": "hero.png",
		}])
		plan = build_product_change_plan("update-csv", {"csv": path, "images": self.image_dir})
		result = apply_product_change_plan(plan)
		self.product.refresh_from_db()
		self.assertEqual(self.product.name, "Chemise Malia")
		self.assertEqual(self.product.description, "Nouvelle description.")
		self.assertEqual(self.product.category, self.other_collection)
		self.assertTrue(self.product.image.name.startswith("products/manager/"))
		self.assertEqual(result["status"], "applied")

	def test_update_form_rejects_csv_publication_field(self):
		path = self.write_csv([{"sku": "YOR-001", "is_active": "true", "name": "Essai"}])
		plan = build_product_change_plan("update-csv", {"csv": path, "images": None})
		self.assertTrue(plan.errors)
		with self.assertRaises(ProductManagerError):
			apply_product_change_plan(plan)
		self.product.refresh_from_db()
		self.assertTrue(self.product.is_active)
		self.assertEqual(self.product.name, "Chemise Naya")

	def test_publish_unpublish_and_prepare_collection_are_approved_changes(self):
		publish = build_product_change_plan("publish", self.options)
		apply_product_change_plan(publish)
		self.product.refresh_from_db()
		self.assertTrue(self.product.is_active)

		unpublish = build_product_change_plan("unpublish", self.options)
		apply_product_change_plan(unpublish)
		self.product.refresh_from_db()
		self.assertFalse(self.product.is_active)

		prepare = build_product_change_plan("prepare-collection", {"collection": "Essentiels"})
		apply_product_change_plan(prepare)
		self.product.refresh_from_db()
		self.collection.refresh_from_db()
		self.assertFalse(self.product.is_active)
		self.assertFalse(self.collection.is_active)
		self.assertTrue(ProductChangeLog.objects.filter(batch__operation="prepare-collection", target_type="category").exists())

	def test_missing_product_and_duplicate_csv_rows_are_conflicts(self):
		missing = self.write_csv([{"sku": "MISSING", "stock": "2"}])
		missing_plan = build_product_change_plan("set-stock", {"csv": missing})
		self.assertTrue(missing_plan.errors)
		with self.assertRaises(ProductManagerError):
			apply_product_change_plan(missing_plan)

		duplicate = self.write_csv([
			{"sku": "YOR-001", "stock": "2"},
			{"sku": "YOR-001", "stock": "3"},
		])
		duplicate_plan = build_product_change_plan("set-stock", {"csv": duplicate})
		self.assertTrue(duplicate_plan.errors)
		self.assertEqual(self.product.stock, 8)

	def test_target_collection_change_after_preview_is_rejected(self):
		plan = build_product_change_plan("update", {**self.options, "target_collection": "Accessoires"})
		Category.objects.filter(pk=self.other_collection.pk).update(name="Renamed")
		with self.assertRaises(StaleProductPlan):
		    apply_product_change_plan(plan)
		self.product.refresh_from_db()
		self.assertEqual(self.product.category_id, self.collection.pk)
		self.assertEqual(ProductChangeBatch.objects.count(), 0)

	def test_setting_same_price_is_a_noop(self):
		plan = build_product_change_plan("set-price", {**self.options, "price": "10000.00"})
		self.assertFalse(plan.has_changes)
		self.assertEqual(plan.rows[0].changes, {})
		self.assertEqual(apply_product_change_plan(plan)["status"], "no_changes")
		self.assertEqual(ProductChangeBatch.objects.count(), 0)
	def test_stale_plan_is_rejected_before_any_write(self):
		plan = build_product_change_plan("set-price", {**self.options, "price": "15000"})
		Product.objects.filter(pk=self.product.pk).update(stock=9)
		with self.assertRaises(StaleProductPlan):
			apply_product_change_plan(plan)
		self.product.refresh_from_db()
		self.assertEqual(self.product.price, Decimal("10000.00"))
		self.assertEqual(ProductChangeBatch.objects.count(), 0)

	def test_retry_after_transaction_error_applies_once(self):
		plan = build_product_change_plan("set-price", {**self.options, "price": "14000"})
		with patch("boutique.services.product_manager.ProductChangeBatch.objects.create", side_effect=IntegrityError("temporary")):
			with self.assertRaises(IntegrityError):
				apply_product_change_plan(plan)
		self.product.refresh_from_db()
		self.assertEqual(self.product.price, Decimal("10000.00"))
		self.assertEqual(ProductChangeBatch.objects.count(), 0)

		first = apply_product_change_plan(plan)
		second = apply_product_change_plan(plan)
		self.assertEqual(first["status"], "applied")
		self.assertTrue(second["already_applied"])
		self.assertEqual(ProductChangeBatch.objects.count(), 1)
		self.assertEqual(ProductChangeLog.objects.count(), 1)

	def test_search_finds_out_of_stock_and_products_without_sku(self):
		self.product.sku = None
		self.product.stock = 0
		self.product.save(update_fields=("sku", "stock", "updated_at"))
		results = build_search_results({"missing_sku": True, "out_of_stock": True, "visibility": "all"})
		self.assertEqual([item["id"] for item in results], [self.product.pk])

	def test_management_command_uses_approval_digest(self):
		plan = build_product_change_plan("set-price", {**self.options, "price": "13000"})
		with override_settings(DEBUG=True):
			with self.assertRaisesRegex(CommandError, "confirm-digest"):
				call_command(
					"product_manager", action="set-price", mode="apply", product_id=self.product.pk,
					price="13000", stdout=None,
				)
			call_command(
				"product_manager", action="set-price", mode="apply", product_id=self.product.pk,
				price="13000", confirm_digest=plan.fingerprint,
			)
		self.product.refresh_from_db()
		self.assertEqual(self.product.price, Decimal("13000.00"))

	def test_command_rejects_digest_if_product_changed_after_preview(self):
		plan = build_product_change_plan("set-price", {**self.options, "price": "13000"})
		Product.objects.filter(pk=self.product.pk).update(stock=9)
		with override_settings(DEBUG=True):
			with self.assertRaisesRegex(CommandError, "changé depuis PREVIEW"):
				call_command(
					"product_manager", action="set-price", mode="apply", product_id=self.product.pk,
					price="13000", confirm_digest=plan.fingerprint,
				)
		self.assertEqual(ProductChangeBatch.objects.count(), 0)
