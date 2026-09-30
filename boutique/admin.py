from django.contrib import admin
from django.contrib.auth.admin import UserAdmin
from django.db import transaction

from .models import Category, Coupon, Order, OrderItem, Product, ProductImage, ReturnRequest, Review, User
from .notifications import send_return_status_email


class OwnerAdminSite(admin.AdminSite):
	site_header = 'Administration Yorwani'
	site_title = 'Maison Yorwani'
	index_title = 'Gestion de la maison'

	def has_permission(self, request):
		return request.user.is_active and request.user.is_superuser


owner_admin_site = OwnerAdminSite(name='owner_admin')


class ProductImageInline(admin.TabularInline):
	model = ProductImage
	extra = 1


class OrderItemInline(admin.TabularInline):
	model = OrderItem
	extra = 0
	readonly_fields = ('product_name', 'size', 'quantity', 'unit_price')
	can_delete = False


@admin.register(User, site=owner_admin_site)
class YorwaniUserAdmin(UserAdmin):
	ordering = ('email',)
	search_fields = ('email', 'first_name', 'last_name')
	readonly_fields = UserAdmin.readonly_fields + ('loyalty_points',)
	fieldsets = UserAdmin.fieldsets + (('Fidélité Yorwani', {'fields': ('loyalty_points',)}),)


@admin.register(Product, site=owner_admin_site)
class ProductAdmin(admin.ModelAdmin):
	list_display = ('name', 'category', 'color', 'price', 'stock', 'is_active', 'is_featured')
	list_filter = ('category', 'color', 'is_active', 'is_featured')
	search_fields = ('name', 'description')
	inlines = (ProductImageInline,)
	prepopulated_fields = {'slug': ('name',)}


@admin.register(Category, site=owner_admin_site)
class CategoryAdmin(admin.ModelAdmin):
	list_display = ('name', 'is_active')
	prepopulated_fields = {'slug': ('name',)}


@admin.register(Order, site=owner_admin_site)
class OrderAdmin(admin.ModelAdmin):
	list_display = ('reference', 'customer_name', 'total', 'payment_status', 'status', 'shipping_carrier', 'tracking_number', 'created_at')
	list_filter = ('payment_status', 'status', 'created_at')
	search_fields = ('reference', 'customer_name', 'customer_email', 'phone')
	readonly_fields = ('reference', 'user', 'customer_name', 'customer_email', 'phone', 'address', 'city', 'delivery_note', 'subtotal', 'shipping_fee', 'discount_amount', 'delivered_at', 'created_at')
	inlines = (OrderItemInline,)


@admin.register(Coupon, site=owner_admin_site)
class CouponAdmin(admin.ModelAdmin):
	list_display = ('code', 'discount_type', 'discount_value', 'minimum_amount', 'uses_count', 'maximum_uses', 'expires_at', 'is_active')
	list_filter = ('is_active', 'discount_type', 'starts_at', 'expires_at')
	search_fields = ('code',)
	readonly_fields = ('uses_count', 'created_at')


@admin.register(Review, site=owner_admin_site)
class ReviewAdmin(admin.ModelAdmin):
	list_display = ('product', 'user', 'rating', 'is_approved', 'created_at')
	list_filter = ('is_approved', 'rating', 'created_at')
	search_fields = ('product__name', 'user__email', 'comment')
	readonly_fields = ('user', 'product', 'rating', 'comment', 'created_at', 'updated_at')


@admin.register(ReturnRequest, site=owner_admin_site)
class ReturnRequestAdmin(admin.ModelAdmin):
	list_display = ('order', 'status', 'created_at', 'updated_at')
	list_filter = ('status', 'created_at')
	search_fields = ('order__reference', 'order__customer_name', 'order__customer_email', 'reason')
	readonly_fields = ('order', 'reason', 'created_at', 'updated_at')
	fields = ('order', 'reason', 'status', 'admin_note', 'created_at', 'updated_at')

	def save_model(self, request, obj, form, change):
		previous_status = ReturnRequest.objects.get(pk=obj.pk).status if change else None
		with transaction.atomic():
			super().save_model(request, obj, form, change)
			if obj.status == ReturnRequest.Status.REFUNDED and previous_status != ReturnRequest.Status.REFUNDED:
				order = Order.objects.select_for_update().get(pk=obj.order_id)
				customer = User.objects.select_for_update().get(pk=order.user_id)
				customer.loyalty_points = max(
					customer.loyalty_points - order.loyalty_points_awarded + order.loyalty_points_used,
					0,
				)
				customer.save(update_fields=('loyalty_points',))
		if change and previous_status != obj.status and not send_return_status_email(obj):
			self.message_user(request, 'Le statut est enregistré, mais l’e-mail client n’a pas pu être envoyé.')
