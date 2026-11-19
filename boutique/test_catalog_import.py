import csv
import hashlib
import tempfile
from pathlib import Path
from io import StringIO
from unittest.mock import patch

from django.core.files.storage import FileSystemStorage
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase, override_settings
from PIL import Image

from boutique.models import CatalogImportBatch, CatalogImportRow, Category, Product
from boutique.services.catalog_import import apply_import_plan, build_import_plan


class CatalogImportTests(TestCase):
	def setUp(self):
		self.tempdir = tempfile.TemporaryDirectory()
		self.addCleanup(self.tempdir.cleanup)
		self.root = Path(self.tempdir.name)
		self.image_dir = self.root / 'images'
		self.image_dir.mkdir()
		self.csv_path = self.root / 'catalog.csv'
		self.storage_dir = self.root / 'storage'
		self.storage_dir.mkdir()
		self.storage_patch = patch(
			'boutique.services.catalog_import.default_storage',
			FileSystemStorage(location=self.storage_dir),
		)
		self.storage_patch.start()
		self.addCleanup(self.storage_patch.stop)

	def write_csv(self, rows, headers=None):
		headers = headers or ['name', 'sku', 'description', 'price', 'collection', 'stock', 'image_filename', 'is_active']
		with self.csv_path.open('w', encoding='utf-8-sig', newline='') as handle:
			writer = csv.DictWriter(handle, fieldnames=headers)
			writer.writeheader()
			writer.writerows(rows)

	def create_image(self, name='shirt.png', color='red'):
		path = self.image_dir / name
		Image.new('RGB', (8, 8), color=color).save(path, format='PNG')
		return path

	def row(self, **overrides):
		data = {
			'name': 'Chemise Naya',
			'sku': 'yor-001',
			'description': 'Chemise en coton.',
			'price': '12500.00',
			'collection': 'Essentiels',
			'stock': '7',
			'image_filename': 'YOR-001.png',
			'is_active': 'true',
		}
		data.update(overrides)
		return data

	def test_preview_is_read_only_and_shows_image_category_and_draft_policy(self):
		self.create_image('YOR-001.png')
		self.write_csv([self.row(image_filename='')])
		plan = build_import_plan(self.csv_path, self.image_dir)

		self.assertEqual(len(plan.rows), 1)
		self.assertEqual(plan.rows[0].image_filename, 'YOR-001.png')
		self.assertEqual(plan.categories_to_create, ['Essentiels'])
		self.assertTrue(plan.rows[0].publication_requested)
		self.assertEqual(Product.objects.count(), 0)
		self.assertEqual(Category.objects.count(), 0)
		self.assertEqual(CatalogImportBatch.objects.count(), 0)
		self.assertEqual(list(self.storage_dir.iterdir()), [])

	def test_apply_creates_category_product_and_image_but_never_publishes(self):
		self.create_image('YOR-001.png')
		self.write_csv([self.row()])
		plan = build_import_plan(self.csv_path, self.image_dir)
		report = apply_import_plan(plan, self.image_dir)

		product = Product.objects.get(sku='YOR-001')
		self.assertFalse(product.is_active)
		self.assertFalse(product.category.is_active)
		self.assertTrue(product.image.name.startswith('products/imports/'))
		self.assertEqual(report['created'], 1)
		self.assertEqual(report['errors'], 0)
		self.assertEqual(report['categories_created'], ['Essentiels'])
		self.assertTrue(CatalogImportRow.objects.get().publication_requested)

	def test_same_batch_can_be_resumed_without_duplicate_products(self):
		self.create_image('YOR-001.png')
		self.write_csv([self.row()])
		plan = build_import_plan(self.csv_path, self.image_dir)
		apply_import_plan(plan, self.image_dir)
		second_report = apply_import_plan(plan, self.image_dir)

		self.assertEqual(Product.objects.count(), 1)
		self.assertEqual(second_report['created'], 0)
		self.assertEqual(second_report['resumed'], 1)
		self.assertEqual(second_report['categories_created'], ['Essentiels'])

	def test_existing_sku_is_skipped_without_updating_existing_product(self):
		category = Category.objects.create(name='Essentiels')
		existing = Product.objects.create(
			category=category,
			sku='YOR-001',
			name='Ancienne chemise',
			description='Ne pas modifier.',
			price='10000.00',
			stock=2,
		)
		self.create_image('YOR-001.png')
		self.write_csv([self.row()])
		plan = build_import_plan(self.csv_path, self.image_dir)
		report = apply_import_plan(plan, self.image_dir)

		existing.refresh_from_db()
		self.assertEqual(Product.objects.count(), 1)
		self.assertEqual(existing.name, 'Ancienne chemise')
		self.assertEqual(existing.stock, 2)
		self.assertEqual(report['skipped'], 1)

	def test_row_errors_do_not_prevent_other_rows_from_importing(self):
		self.create_image('YOR-002.png')
		self.write_csv([
			self.row(name='Image absente', sku='YOR-001', image_filename='missing.png'),
			self.row(name='Chemise valide', sku='YOR-002', image_filename='YOR-002.png'),
		])
		plan = build_import_plan(self.csv_path, self.image_dir)
		report = apply_import_plan(plan, self.image_dir)

		self.assertEqual(Product.objects.count(), 1)
		self.assertEqual(Product.objects.get().sku, 'YOR-002')
		self.assertEqual(report['created'], 1)
		self.assertEqual(report['errors'], 1)
		self.assertEqual(report['batch_status'], CatalogImportBatch.Status.PARTIAL)

	def test_image_collision_is_reported_and_existing_file_is_not_overwritten(self):
		image_path = self.create_image('YOR-001.png', color='red')
		self.write_csv([self.row()])
		plan = build_import_plan(self.csv_path, self.image_dir)
		image_hash = hashlib.sha256(image_path.read_bytes()).hexdigest()
		storage_key = f'products/imports/{image_hash[:2]}/{image_hash}.png'
		collision_path = self.storage_dir / storage_key
		collision_path.parent.mkdir(parents=True)
		collision_path.write_bytes(b'pre-existing object')

		report = apply_import_plan(plan, self.image_dir)

		self.assertEqual(report['errors'], 1)
		self.assertEqual(Product.objects.count(), 0)
		self.assertEqual(collision_path.read_bytes(), b'pre-existing object')

	def test_csv_category_and_collection_disagreement_is_an_error(self):
		self.create_image('YOR-001.png')
		row = self.row(category='Accessoires')
		self.write_csv([row], headers=['name', 'sku', 'description', 'price', 'category', 'collection', 'stock', 'image_filename', 'is_active'])
		plan = build_import_plan(self.csv_path, self.image_dir)
		self.assertTrue(plan.rows[0].errors)
		self.assertEqual(Product.objects.count(), 0)

	def test_command_requires_approval_digest_before_apply(self):
		self.create_image('YOR-001.png')
		self.write_csv([self.row()])
		with override_settings(DEBUG=True):
			with self.assertRaisesRegex(CommandError, 'confirm-digest'):
				call_command(
					'catalog_import',
					mode='apply',
					file=str(self.csv_path),
					images=str(self.image_dir),
				)
		self.assertEqual(Product.objects.count(), 0)

	def test_changed_input_is_rejected_after_preview_approval(self):
		self.create_image('YOR-001.png')
		self.write_csv([self.row()])
		old_plan = build_import_plan(self.csv_path, self.image_dir)
		self.write_csv([self.row(price='13000')])
		with override_settings(DEBUG=True):
			with self.assertRaisesRegex(CommandError, 'empreinte diffère'):
				call_command(
					'catalog_import', mode='apply', file=str(self.csv_path), images=str(self.image_dir),
					confirm_digest=old_plan.fingerprint,
				)
		self.assertEqual(Product.objects.count(), 0)

	def test_debug_off_requires_explicit_production_permission(self):
		self.write_csv([self.row(image_filename='')])
		with override_settings(DEBUG=False):
			with self.assertRaisesRegex(CommandError, '--allow-production'):
				call_command(
					'catalog_import', mode='preview', file=str(self.csv_path), images=str(self.image_dir),
				)

	def test_command_preview_digest_approves_exact_apply_input(self):
		self.create_image('YOR-001.png')
		self.write_csv([self.row()])
		plan = build_import_plan(self.csv_path, self.image_dir)
		preview_output = StringIO()
		with override_settings(DEBUG=True):
			call_command(
				'catalog_import', mode='preview', file=str(self.csv_path), images=str(self.image_dir), stdout=preview_output,
			)
		self.assertIn(plan.fingerprint, preview_output.getvalue())
		self.assertEqual(Product.objects.count(), 0)

		apply_output = StringIO()
		with override_settings(DEBUG=True):
			call_command(
				'catalog_import', mode='apply', file=str(self.csv_path), images=str(self.image_dir),
				confirm_digest=plan.fingerprint, stdout=apply_output,
			)
		self.assertEqual(Product.objects.count(), 1)
		self.assertFalse(Product.objects.get().is_active)

	def test_duplicate_skus_in_csv_are_all_rejected(self):
		self.create_image('YOR-001.png')
		self.write_csv([
			self.row(name='Chemise 1'),
			self.row(name='Chemise 2'),
		])
		plan = build_import_plan(self.csv_path, self.image_dir)
		self.assertTrue(all(row.errors for row in plan.rows))
		report = apply_import_plan(plan, self.image_dir)
		self.assertEqual(report['created'], 0)
		self.assertEqual(report['errors'], 2)
		self.assertEqual(Product.objects.count(), 0)

	def test_french_headers_semicolon_and_sku_filename_matching(self):
		self.create_image('YOR-002.png')
		with self.csv_path.open('w', encoding='utf-8-sig', newline='') as handle:
			writer = csv.DictWriter(handle, delimiter=';', fieldnames=[
				'Nom du produit', 'SKU si disponible', 'Description', 'Prix', 'Collection',
				'Stock', 'Nom du fichier image', 'Statut de publication souhaité',
			])
			writer.writeheader()
			writer.writerow({
				'Nom du produit': 'Chemise Naya', 'SKU si disponible': 'YOR-002',
				'Description': 'Chemise en coton.', 'Prix': '12500', 'Collection': 'Essentiels',
				'Stock': '7', 'Nom du fichier image': '', 'Statut de publication souhaité': 'non',
			})
		plan = build_import_plan(self.csv_path, self.image_dir)

		self.assertFalse(plan.rows[0].errors)
		self.assertEqual(plan.rows[0].image_filename, 'YOR-002.png')
		self.assertEqual(plan.rows[0].sku, 'YOR-002')
