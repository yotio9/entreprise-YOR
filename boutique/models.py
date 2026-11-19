import uuid
from decimal import Decimal

from django.contrib.auth.models import AbstractUser
from django.core.validators import MaxValueValidator, MinValueValidator, RegexValidator
from django.db import models
from django.utils import timezone
from django.utils.text import slugify


class User(AbstractUser):
	email = models.EmailField(unique=True)
	loyalty_points = models.PositiveIntegerField(default=0)
	phone = models.CharField(
		max_length=10,
		blank=True,
		validators=[RegexValidator(r'^[0-9]{10}$', 'Le numéro doit contenir exactement 10 chiffres.')],
	)

	def __str__(self):
		return self.email


class Category(models.Model):
	name = models.CharField(max_length=80, unique=True)
	slug = models.SlugField(max_length=100, unique=True, blank=True)
	description = models.CharField(max_length=240, blank=True)
	image = models.ImageField(upload_to='collections/', blank=True)
	is_active = models.BooleanField(default=True)

	class Meta:
		ordering = ['name']
		verbose_name_plural = 'categories'

	def save(self, *args, **kwargs):
		if not self.slug:
			self.slug = slugify(self.name)
		super().save(*args, **kwargs)

	def __str__(self):
		return self.name


class Product(models.Model):
	class ZodiacSign(models.TextChoices):
		ARIES = 'aries', 'Bélier'
		TAURUS = 'taurus', 'Taureau'
		GEMINI = 'gemini', 'Gémeaux'
		CANCER = 'cancer', 'Cancer'
		LEO = 'leo', 'Lion'
		VIRGO = 'virgo', 'Vierge'
		LIBRA = 'libra', 'Balance'
		SCORPIO = 'scorpio', 'Scorpion'
		SAGITTARIUS = 'sagittarius', 'Sagittaire'
		CAPRICORN = 'capricorn', 'Capricorne'
		AQUARIUS = 'aquarius', 'Verseau'
		PISCES = 'pisces', 'Poissons'

	category = models.ForeignKey(Category, on_delete=models.PROTECT, related_name='products')
	sku = models.CharField(max_length=80, unique=True, null=True, blank=True)
	name = models.CharField(max_length=160)
	slug = models.SlugField(max_length=180, unique=True, blank=True)
	zodiac_sign = models.CharField(max_length=12, choices=ZodiacSign.choices, blank=True)
	description = models.TextField()
	material = models.CharField(max_length=160, blank=True)
	color = models.CharField(max_length=80, blank=True)
	size_guide = models.TextField(blank=True)
	care_instructions = models.TextField(blank=True)
	price = models.DecimalField(max_digits=12, decimal_places=2)
	compare_at_price = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
	sizes = models.JSONField(default=list, blank=True)
	stock = models.PositiveIntegerField(default=0)
	image = models.ImageField(upload_to='products/', blank=True)
	image_url = models.URLField(blank=True)
	source_url = models.URLField(max_length=2000, blank=True, verbose_name='Lien de provenance')
	source_price_limit = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True, validators=[MinValueValidator(Decimal('0.01'))], verbose_name='Prix fournisseur maximum (FCFA)')
	is_featured = models.BooleanField(default=False)
	is_active = models.BooleanField(default=True)
	created_at = models.DateTimeField(auto_now_add=True)
	updated_at = models.DateTimeField(auto_now=True)

	class Meta:
		ordering = ['-is_featured', '-created_at']

	def save(self, *args, **kwargs):
		self.sku = (self.sku or '').strip().upper() or None
		if not self.slug:
			base_slug = slugify(self.name) or uuid.uuid4().hex[:8]
			self.slug = base_slug
			if Product.objects.filter(slug=self.slug).exclude(pk=self.pk).exists():
				self.slug = f'{base_slug}-{uuid.uuid4().hex[:6]}'
		super().save(*args, **kwargs)

	def __str__(self):
		return self.name


class ProductImage(models.Model):
	product = models.ForeignKey(Product, on_delete=models.CASCADE, related_name='images')
	image = models.ImageField(upload_to='products/gallery/', blank=True)
	image_url = models.URLField(blank=True)
	alt_text = models.CharField(max_length=160, blank=True)
	position = models.PositiveSmallIntegerField(default=0)

	class Meta:
		ordering = ['position', 'id']

	def __str__(self):
		return f'Image de {self.product.name}'


class CatalogImportBatch(models.Model):
	class Status(models.TextChoices):
		APPLYING = 'applying', 'En cours'
		COMPLETED = 'completed', 'Completed'
		PARTIAL = 'partial', 'Partiel'

	fingerprint = models.CharField(max_length=64, unique=True)
	source_filename = models.CharField(max_length=255)
	status = models.CharField(max_length=12, choices=Status.choices, default=Status.APPLYING)
	created_at = models.DateTimeField(auto_now_add=True)
	updated_at = models.DateTimeField(auto_now=True)

	class Meta:
		ordering = ['-created_at']

	def __str__(self):
		return f'Import catalogue {self.source_filename} ({self.status})'


class CatalogImportRow(models.Model):
	class Status(models.TextChoices):
		PENDING = 'pending', 'En attente'
		RUNNING = 'running', 'En cours'
		CREATED = 'created', 'Created'
		SKIPPED = 'skipped', 'Skipped'
		ERROR = 'error', 'Erreur'

	batch = models.ForeignKey(CatalogImportBatch, on_delete=models.CASCADE, related_name='rows')
	row_number = models.PositiveIntegerField()
	product = models.ForeignKey(Product, on_delete=models.SET_NULL, null=True, blank=True, related_name='catalog_import_rows')
	sku = models.CharField(max_length=80, blank=True)
	product_name = models.CharField(max_length=160, blank=True)
	publication_requested = models.BooleanField(default=False)
	category_created = models.BooleanField(default=False)
	status = models.CharField(max_length=12, choices=Status.choices, default=Status.PENDING)
	message = models.TextField(blank=True)
	updated_at = models.DateTimeField(auto_now=True)

	class Meta:
		ordering = ['row_number']
		constraints = [
			models.UniqueConstraint(fields=('batch', 'row_number'), name='unique_catalog_import_batch_row'),
		]

	def __str__(self):
		return f'{self.batch.source_filename} ligne {self.row_number}: {self.status}'


class ProductChangeBatch(models.Model):
	fingerprint = models.CharField(max_length=64, unique=True)
	operation = models.CharField(max_length=32)
	status = models.CharField(max_length=12, default='applied')
	details = models.JSONField(default=dict, blank=True)
	created_at = models.DateTimeField(auto_now_add=True)

	class Meta:
		ordering = ['-created_at']

	def __str__(self):
		return f'{self.operation} {self.fingerprint[:12]}'


class ProductChangeLog(models.Model):
	class TargetType(models.TextChoices):
		PRODUCT = 'product', 'Product'
		CATEGORY = 'category', 'Category'

	batch = models.ForeignKey(ProductChangeBatch, on_delete=models.PROTECT, related_name='changes')
	target_type = models.CharField(max_length=8, choices=TargetType.choices)
	target_id_snapshot = models.PositiveBigIntegerField()
	label_snapshot = models.CharField(max_length=160)
	sku_snapshot = models.CharField(max_length=80, blank=True)
	product = models.ForeignKey(Product, on_delete=models.SET_NULL, null=True, blank=True, related_name='change_logs')
	category = models.ForeignKey(Category, on_delete=models.SET_NULL, null=True, blank=True, related_name='change_logs')
	before = models.JSONField(default=dict)
	after = models.JSONField(default=dict)
	created_at = models.DateTimeField(auto_now_add=True)

	class Meta:
		ordering = ['id']
		constraints = [
			models.UniqueConstraint(
				fields=('batch', 'target_type', 'target_id_snapshot'),
				name='unique_product_change_target_per_batch',
			),
		]

	def __str__(self):
		return f'{self.batch.operation}: {self.label_snapshot}'


class OrderChangeBatch(models.Model):
	fingerprint = models.CharField(max_length=64, unique=True)
	operation = models.CharField(max_length=16)
	status = models.CharField(max_length=12, default='applied')
	details = models.JSONField(default=dict, blank=True)
	created_at = models.DateTimeField(auto_now_add=True)

	class Meta:
		ordering = ['-created_at']

	def __str__(self):
		return f'{self.operation} {self.fingerprint[:12]}'


class OrderChangeLog(models.Model):
	batch = models.ForeignKey(OrderChangeBatch, on_delete=models.PROTECT, related_name='changes')
	order = models.ForeignKey('Order', on_delete=models.PROTECT, related_name='change_logs')
	reference_snapshot = models.CharField(max_length=20)
	before = models.JSONField(default=dict)
	after = models.JSONField(default=dict)
	created_at = models.DateTimeField(auto_now_add=True)

	class Meta:
		ordering = ['id']
		constraints = [
			models.UniqueConstraint(fields=('batch', 'order'), name='unique_order_change_per_batch'),
		]

	def __str__(self):
		return f'{self.batch.operation}: {self.reference_snapshot}'


class CustomerChangeBatch(models.Model):
	fingerprint = models.CharField(max_length=64, unique=True)
	operation = models.CharField(max_length=16)
	status = models.CharField(max_length=12, default='applied')
	details = models.JSONField(default=dict, blank=True)
	created_at = models.DateTimeField(auto_now_add=True)

	class Meta:
		ordering = ['-created_at']

	def __str__(self):
		return f'{self.operation} {self.fingerprint[:12]}'


class CustomerChangeLog(models.Model):
	batch = models.ForeignKey(CustomerChangeBatch, on_delete=models.PROTECT, related_name='changes')
	customer = models.ForeignKey('User', on_delete=models.PROTECT, related_name='change_logs')
	customer_id_snapshot = models.PositiveBigIntegerField()
	before = models.JSONField(default=dict)
	after = models.JSONField(default=dict)
	created_at = models.DateTimeField(auto_now_add=True)

	class Meta:
		ordering = ['id']
		constraints = [
			models.UniqueConstraint(fields=('batch', 'customer'), name='unique_customer_change_per_batch'),
		]

	def __str__(self):
		return f'{self.batch.operation}: customer {self.customer_id_snapshot}'


def generate_order_reference():
	return f'YOR-{uuid.uuid4().hex[:10].upper()}'


class Order(models.Model):
	class Status(models.TextChoices):
		AWAITING_PAYMENT = 'awaiting_payment', 'En attente de paiement'
		CONFIRMED = 'confirmed', 'Confirmée'
		PREPARING = 'preparing', 'En préparation'
		SHIPPED = 'shipped', 'Expédiée'
		DELIVERED = 'delivered', 'Livrée'
		CANCELLED = 'cancelled', 'Annulée'

	class PaymentMethod(models.TextChoices):
		WAVE = 'wave', 'Wave maintenant'
		CASH_ON_DELIVERY = 'cash_on_delivery', 'Paiement à la livraison'

	class PaymentStatus(models.TextChoices):
		PENDING = 'pending', 'À vérifier'
		DUE_ON_DELIVERY = 'due_on_delivery', 'À régler à la livraison'
		CONFIRMED = 'confirmed', 'Paiement confirmé'

	reference = models.CharField(max_length=20, unique=True, default=generate_order_reference)
	user = models.ForeignKey('boutique.User', on_delete=models.PROTECT, related_name='orders')
	status = models.CharField(max_length=24, choices=Status.choices, default=Status.AWAITING_PAYMENT)
	payment_status = models.CharField(max_length=16, choices=PaymentStatus.choices, default=PaymentStatus.PENDING)
	payment_method = models.CharField(max_length=20, choices=PaymentMethod.choices, default=PaymentMethod.WAVE)
	customer_name = models.CharField(max_length=160)
	customer_email = models.EmailField()
	phone = models.CharField(max_length=32)
	address = models.CharField(max_length=240)
	city = models.CharField(max_length=100)
	delivery_note = models.TextField(blank=True)
	tracking_number = models.CharField(max_length=100, blank=True)
	shipping_carrier = models.CharField(max_length=100, blank=True)
	delivered_at = models.DateTimeField(null=True, blank=True)
	wave_checkout_id = models.CharField(max_length=32, blank=True, null=True, unique=True)
	wave_launch_url = models.URLField(max_length=2000, blank=True)
	wave_transaction_id = models.CharField(max_length=64, blank=True)
	subtotal = models.DecimalField(max_digits=12, decimal_places=2, default=0)
	shipping_fee = models.DecimalField(max_digits=12, decimal_places=2, default=0)
	discount_amount = models.DecimalField(max_digits=12, decimal_places=2, default=0)
	loyalty_points_used = models.PositiveIntegerField(default=0)
	loyalty_points_awarded = models.PositiveIntegerField(default=0)
	coupon = models.ForeignKey('Coupon', on_delete=models.SET_NULL, null=True, blank=True, related_name='orders')
	created_at = models.DateTimeField(auto_now_add=True)

	class Meta:
		ordering = ['-created_at']

	@property
	def total(self):
		return max(self.subtotal + self.shipping_fee - self.discount_amount, 0)

	def __str__(self):
		return self.reference


class OrderItem(models.Model):
	order = models.ForeignKey(Order, on_delete=models.CASCADE, related_name='items')
	product = models.ForeignKey(Product, on_delete=models.SET_NULL, null=True, blank=True, related_name='order_items')
	product_name = models.CharField(max_length=160)
	size = models.CharField(max_length=32, blank=True)
	personalization = models.JSONField(default=dict, blank=True)
	quantity = models.PositiveSmallIntegerField()
	unit_price = models.DecimalField(max_digits=12, decimal_places=2)

	@property
	def line_total(self):
		return self.unit_price * self.quantity

	def __str__(self):
		return f'{self.quantity} x {self.product_name}'


class Favorite(models.Model):
	user = models.ForeignKey('boutique.User', on_delete=models.CASCADE, related_name='favorites')
	product = models.ForeignKey(Product, on_delete=models.CASCADE, related_name='favorited_by')
	created_at = models.DateTimeField(auto_now_add=True)

	class Meta:
		constraints = [models.UniqueConstraint(fields=('user', 'product'), name='unique_user_favorite_product')]
		ordering = ['-created_at']

	def __str__(self):
		return f'{self.user.email} · {self.product.name}'


class Review(models.Model):
	user = models.ForeignKey('boutique.User', on_delete=models.CASCADE, related_name='product_reviews')
	product = models.ForeignKey(Product, on_delete=models.CASCADE, related_name='reviews')
	rating = models.PositiveSmallIntegerField(validators=[MinValueValidator(1), MaxValueValidator(5)])
	comment = models.TextField(max_length=1200, blank=True)
	is_approved = models.BooleanField(default=True)
	created_at = models.DateTimeField(auto_now_add=True)
	updated_at = models.DateTimeField(auto_now=True)

	class Meta:
		constraints = [models.UniqueConstraint(fields=('user', 'product'), name='unique_user_product_review')]
		ordering = ['-created_at']

	def __str__(self):
		return f'{self.product.name} · {self.rating}/5'


class Coupon(models.Model):
	class DiscountType(models.TextChoices):
		PERCENT = 'percent', 'Pourcentage'
		FIXED = 'fixed', 'Montant fixe'

	code = models.CharField(max_length=32, unique=True)
	discount_type = models.CharField(max_length=8, choices=DiscountType.choices, default=DiscountType.PERCENT)
	discount_value = models.DecimalField(max_digits=10, decimal_places=2, validators=[MinValueValidator(Decimal('0.01'))])
	minimum_amount = models.DecimalField(max_digits=12, decimal_places=2, default=0)
	starts_at = models.DateTimeField(default=timezone.now)
	expires_at = models.DateTimeField(null=True, blank=True)
	maximum_uses = models.PositiveIntegerField(null=True, blank=True)
	uses_count = models.PositiveIntegerField(default=0)
	is_active = models.BooleanField(default=True)
	created_at = models.DateTimeField(auto_now_add=True)

	class Meta:
		ordering = ['code']

	def clean_code(self):
		return self.code.strip().upper()

	def is_valid_for(self, subtotal, now=None):
		now = now or timezone.now()
		return (
			self.is_active
			and self.starts_at <= now
			and (self.expires_at is None or self.expires_at >= now)
			and (self.maximum_uses is None or self.uses_count < self.maximum_uses)
			and subtotal >= self.minimum_amount
		)

	def calculate_discount(self, subtotal):
		if self.discount_type == self.DiscountType.PERCENT:
			discount = subtotal * self.discount_value / Decimal('100')
		else:
			discount = self.discount_value
		return min(discount.quantize(Decimal('0.01')), subtotal)

	def save(self, *args, **kwargs):
		self.code = self.code.strip().upper()
		super().save(*args, **kwargs)

	def __str__(self):
		return self.code


class ReturnRequest(models.Model):
	class RequestType(models.TextChoices):
		RETURN = 'return', 'Retour / remboursement'
		EXCHANGE = 'exchange', 'Échange'

	class Status(models.TextChoices):
		PENDING = 'pending', 'À examiner'
		APPROVED = 'approved', 'Acceptée'
		REJECTED = 'rejected', 'Refusée'
		RECEIVED = 'received', 'Article reçu'
		REFUNDED = 'refunded', 'Remboursée'

	order = models.OneToOneField(Order, on_delete=models.CASCADE, related_name='return_request')
	request_type = models.CharField(max_length=8, choices=RequestType.choices, default=RequestType.RETURN)
	reason = models.TextField(max_length=1200)
	status = models.CharField(max_length=12, choices=Status.choices, default=Status.PENDING)
	admin_note = models.TextField(blank=True)
	created_at = models.DateTimeField(auto_now_add=True)
	updated_at = models.DateTimeField(auto_now=True)

	class Meta:
		ordering = ['-created_at']

	def __str__(self):
		return f'Retour {self.order.reference} · {self.get_status_display()}'


class JumiaOrderTask(models.Model):
	class Status(models.TextChoices):
		PENDING = 'pending', 'En attente'
		RUNNING = 'running', 'En cours'
		PREPARED = 'prepared', 'Preparee - revue humaine requise'
		NEEDS_HUMAN = 'needs_human', 'Verification humaine requise'
		FAILED = 'failed', 'Echec - reprise possible'

	order_item = models.OneToOneField(OrderItem, on_delete=models.PROTECT, related_name='jumia_task')
	source_url = models.URLField(max_length=2000)
	expected_product_name = models.CharField(max_length=160)
	expected_color = models.CharField(max_length=80, blank=True)
	expected_size = models.CharField(max_length=32, blank=True)
	quantity = models.PositiveSmallIntegerField()
	price_limit = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
	status = models.CharField(max_length=16, choices=Status.choices, default=Status.PENDING)
	simulation = models.BooleanField(default=True, editable=False)
	attempt_count = models.PositiveSmallIntegerField(default=0)
	max_attempts = models.PositiveSmallIntegerField(default=3)
	available_at = models.DateTimeField(default=timezone.now)
	claimed_at = models.DateTimeField(null=True, blank=True)
	verified_title = models.CharField(max_length=300, blank=True)
	verified_price = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
	verified_currency = models.CharField(max_length=8, blank=True)
	availability = models.CharField(max_length=24, blank=True)
	result_summary = models.TextField(blank=True)
	last_error_code = models.CharField(max_length=48, blank=True)
	result_data = models.JSONField(default=dict, blank=True)
	created_at = models.DateTimeField(auto_now_add=True)
	updated_at = models.DateTimeField(auto_now=True)

	class Meta:
		ordering = ['created_at']
		constraints = [
			models.CheckConstraint(condition=models.Q(simulation=True), name='jumia_task_simulation_only'),
		]
		indexes = [
			models.Index(fields=('status', 'available_at'), name='jumia_task_ready_idx'),
			models.Index(fields=('status', 'claimed_at'), name='jumia_task_lease_idx'),
		]

	@property
	def order(self):
		return self.order_item.order

	def __str__(self):
		return f'Jumia | {self.order_item.order.reference} | {self.expected_product_name}'


class JumiaOrderAttempt(models.Model):
	class Status(models.TextChoices):
		STARTED = 'started', 'Demarree'
		PREPARED = 'prepared', 'Preparee'
		NEEDS_HUMAN = 'needs_human', 'Revue humaine'
		RETRYABLE_FAILURE = 'retryable_failure', 'Erreur temporaire'
		PERMANENT_FAILURE = 'permanent_failure', 'Erreur definitive'
		INTERRUPTED = 'interrupted', 'Interrompue'

	task = models.ForeignKey(JumiaOrderTask, on_delete=models.CASCADE, related_name='attempts')
	number = models.PositiveSmallIntegerField()
	status = models.CharField(max_length=24, choices=Status.choices, default=Status.STARTED)
	summary = models.TextField(blank=True)
	result_data = models.JSONField(default=dict, blank=True)
	started_at = models.DateTimeField(auto_now_add=True)
	finished_at = models.DateTimeField(null=True, blank=True)

	class Meta:
		ordering = ['number']
		constraints = [
			models.UniqueConstraint(fields=('task', 'number'), name='unique_jumia_task_attempt_number'),
		]

	def __str__(self):
		return f'{self.task_id} | tentative {self.number}'
