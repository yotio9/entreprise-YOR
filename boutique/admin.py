from django.contrib import admin
from django.contrib.auth.admin import UserAdmin

from .models import Category, Order, OrderItem, Product, ProductImage, User


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


@admin.register(Product, site=owner_admin_site)
class ProductAdmin(admin.ModelAdmin):
	list_display = ('name', 'category', 'price', 'stock', 'is_active', 'is_featured')
	list_filter = ('category', 'is_active', 'is_featured')
	search_fields = ('name', 'description')
	inlines = (ProductImageInline,)
	prepopulated_fields = {'slug': ('name',)}


@admin.register(Category, site=owner_admin_site)
class CategoryAdmin(admin.ModelAdmin):
	list_display = ('name', 'is_active')
	prepopulated_fields = {'slug': ('name',)}


@admin.register(Order, site=owner_admin_site)
class OrderAdmin(admin.ModelAdmin):
	list_display = ('reference', 'customer_name', 'total', 'payment_status', 'status', 'created_at')
	list_filter = ('payment_status', 'status', 'created_at')
	search_fields = ('reference', 'customer_name', 'customer_email', 'phone')
	readonly_fields = ('reference', 'user', 'customer_name', 'customer_email', 'phone', 'address', 'city', 'delivery_note', 'subtotal', 'shipping_fee', 'created_at')
	inlines = (OrderItemInline,)
