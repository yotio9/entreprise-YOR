from decimal import Decimal, InvalidOperation
from datetime import timedelta
import csv
import hashlib
import hmac
import json
import logging
import time
from urllib.parse import urlparse

from django.conf import settings
from django.contrib import messages
from django.contrib.auth import get_user_model, login, logout
from django.contrib.auth.decorators import login_required, user_passes_test
from django.db import transaction
from django.db.models import Avg, Count, Q, Sum
from django.http import Http404, HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST

from .forms import EmailAuthenticationForm, FulfillmentForm, ProductForm, RegistrationForm, ReturnRequestForm, ReviewForm
from .models import Category, Coupon, Favorite, Order, OrderItem, Product, ProductImage, ReturnRequest, Review
from .notifications import (
	send_new_order_notification_email,
	send_order_received_email,
	send_order_status_email,
	send_return_request_notifications,
	send_payment_confirmed_email,
	send_wave_payment_link_email,
)
from .wave import WaveCheckoutError, create_checkout_session, create_merchant_payment_link

logger = logging.getLogger(__name__)


def home(request):
	categories = Category.objects.filter(is_active=True)
	featured = Product.objects.filter(is_active=True, is_featured=True, stock__gt=0)[:4]
	if not featured:
		featured = Product.objects.filter(is_active=True, stock__gt=0)[:4]
	favorite_ids = set(Favorite.objects.filter(user=request.user).values_list('product_id', flat=True)) if request.user.is_authenticated else set()
	return render(request, 'boutique/home.html', {'categories': categories, 'featured': featured, 'favorite_ids': favorite_ids})


def catalogue(request):
	products = Product.objects.filter(is_active=True).select_related('category')
	categories = Category.objects.filter(is_active=True)
	category_slug = request.GET.get('categorie', '')
	selected_size = request.GET.get('taille', '').strip().upper()
	search = request.GET.get('q', '').strip()
	selected_color = request.GET.get('couleur', '').strip()
	selected_sort = request.GET.get('tri', 'featured')
	minimum_price = request.GET.get('prix_min', '').strip()
	maximum_price = request.GET.get('prix_max', '').strip()
	in_stock = request.GET.get('disponible') == '1'
	if category_slug:
		products = products.filter(category__slug=category_slug)
	if search:
		products = products.filter(
			Q(name__icontains=search)
			| Q(description__icontains=search)
			| Q(material__icontains=search)
			| Q(color__icontains=search)
			| Q(category__name__icontains=search)
		)
	if selected_color:
		products = products.filter(color__iexact=selected_color)
	for value, lookup, label in (
		(minimum_price, 'price__gte', 'minimum'),
		(maximum_price, 'price__lte', 'maximum'),
	):
		if value:
			try:
				products = products.filter(**{lookup: Decimal(value)})
			except InvalidOperation:
				if label == 'minimum':
					minimum_price = ''
				else:
					maximum_price = ''
	if in_stock:
		products = products.filter(stock__gt=0)
	if selected_size:
		products = [product for product in products if selected_size in product.sizes]
	if selected_sort == 'price_asc':
		products = products.order_by('price', 'name') if hasattr(products, 'order_by') else sorted(products, key=lambda product: product.price)
	elif selected_sort == 'price_desc':
		products = products.order_by('-price', 'name') if hasattr(products, 'order_by') else sorted(products, key=lambda product: product.price, reverse=True)
	elif selected_sort == 'name':
		products = products.order_by('name') if hasattr(products, 'order_by') else sorted(products, key=lambda product: product.name.lower())
	elif selected_sort == 'rating':
		if hasattr(products, 'annotate'):
			products = products.annotate(review_average=Avg('reviews__rating', filter=Q(reviews__is_approved=True))).order_by('-review_average', '-created_at')
		else:
			products = sorted(products, key=lambda product: product.name.lower())
	else:
		selected_sort = 'featured'
	colors = Product.objects.filter(is_active=True).exclude(color='').order_by('color').values_list('color', flat=True).distinct()
	favorite_ids = set()
	if request.user.is_authenticated:
		favorite_ids = set(Favorite.objects.filter(user=request.user).values_list('product_id', flat=True))
	return render(request, 'boutique/catalogue.html', {
		'products': products,
		'categories': categories,
		'selected_category': category_slug,
		'selected_size': selected_size,
		'search': search,
		'colors': colors,
		'selected_color': selected_color,
		'selected_sort': selected_sort,
		'minimum_price': minimum_price,
		'maximum_price': maximum_price,
		'in_stock': in_stock,
		'favorite_ids': favorite_ids,
	})


def product_detail(request, slug):
	product = get_object_or_404(Product, slug=slug, is_active=True)
	verified_buyer = request.user.is_authenticated and OrderItem.objects.filter(
		product=product,
		order__user=request.user,
		order__payment_status=Order.PaymentStatus.CONFIRMED,
	).exclude(order__status=Order.Status.CANCELLED).exists()
	user_review = Review.objects.filter(product=product, user=request.user).first() if request.user.is_authenticated else None
	reviews = product.reviews.filter(is_approved=True).select_related('user')
	product_stats = reviews.aggregate(average=Avg('rating'), count=Count('id'))
	favorite_ids = set(Favorite.objects.filter(user=request.user).values_list('product_id', flat=True)) if request.user.is_authenticated else set()
	recommendations = Product.objects.filter(
		category=product.category,
		is_active=True,
		stock__gt=0,
	).exclude(pk=product.pk).annotate(
		review_average=Avg('reviews__rating', filter=Q(reviews__is_approved=True)),
	).order_by('-is_featured', '-review_average', '-created_at')[:4]
	favorited = request.user.is_authenticated and Favorite.objects.filter(user=request.user, product=product).exists()
	return render(request, 'boutique/product_detail.html', {
		'product': product,
		'gallery': product.images.all(),
		'reviews': reviews,
		'review_average': product_stats['average'],
		'review_count': product_stats['count'],
		'verified_buyer': verified_buyer,
		'user_review': user_review,
		'review_form': ReviewForm(instance=user_review),
		'recommendations': recommendations,
		'favorited': favorited,
		'favorite_ids': favorite_ids,
	})


@login_required
@require_POST
def toggle_favorite(request, product_id):
	product = get_object_or_404(Product, pk=product_id, is_active=True)
	favorite, created = Favorite.objects.get_or_create(user=request.user, product=product)
	if not created:
		favorite.delete()
		messages.info(request, 'Article retiré de vos favoris.')
	else:
		messages.success(request, 'Article ajouté à vos favoris.')
	next_url = request.POST.get('next', '')
	if next_url and url_has_allowed_host_and_scheme(next_url, allowed_hosts={request.get_host()}, require_https=request.is_secure()):
		return redirect(next_url)
	return redirect('produit', slug=product.slug)


@login_required
def favorite_list(request):
	products = Product.objects.filter(favorited_by__user=request.user, is_active=True).select_related('category')
	favorite_ids = set(products.values_list('id', flat=True))
	return render(request, 'boutique/favorites.html', {'products': products, 'favorite_ids': favorite_ids})


@login_required
@require_POST
def submit_review(request, slug):
	product = get_object_or_404(Product, slug=slug, is_active=True)
	if not OrderItem.objects.filter(
		product=product,
		order__user=request.user,
		order__payment_status=Order.PaymentStatus.CONFIRMED,
	).exclude(order__status=Order.Status.CANCELLED).exists():
		messages.error(request, 'Un achat de cet article est nécessaire pour laisser un avis vérifié.')
		return redirect('produit', slug=slug)
	review = Review.objects.filter(user=request.user, product=product).first()
	form = ReviewForm(request.POST, instance=review)
	if form.is_valid():
		review = form.save(commit=False)
		review.user = request.user
		review.product = product
		review.is_approved = True
		review.save()
		messages.success(request, 'Merci, votre avis vérifié a été publié.')
	else:
		messages.error(request, 'Vérifiez la note et le texte de votre avis.')
	return redirect('produit', slug=slug)


def _cart_lines(request):
	cart = request.session.get('cart', {})
	product_ids = [item.get('product_id') for item in cart.values() if item.get('product_id')]
	products = {product.id: product for product in Product.objects.filter(id__in=product_ids, is_active=True)}
	lines = []
	for key, entry in cart.items():
		product = products.get(entry.get('product_id'))
		if product:
			quantity = min(max(int(entry.get('quantity', 1)), 1), product.stock)
			if quantity:
				lines.append({
					'key': key,
					'product': product,
					'quantity': quantity,
					'size': entry.get('size', ''),
					'line_total': product.price * quantity,
				})
	subtotal = sum((line['line_total'] for line in lines), Decimal('0.00'))
	return lines, subtotal


def _cart_json(request):
	lines, subtotal = _cart_lines(request)
	return {
		'count': sum(line['quantity'] for line in lines),
		'subtotal': str(subtotal),
		'lines': [{
			'key': line['key'],
			'quantity': line['quantity'],
			'line_total': str(line['line_total']),
		} for line in lines],
	}


def cart_detail(request):
	lines, subtotal = _cart_lines(request)
	coupon = Coupon.objects.filter(code=request.session.get('coupon_code', '')).first()
	discount = coupon.calculate_discount(subtotal) if coupon and coupon.is_valid_for(subtotal) else Decimal('0.00')
	if not discount:
		coupon = None
		request.session.pop('coupon_code', None)
	return render(request, 'boutique/cart.html', {
		'lines': lines,
		'subtotal': subtotal,
		'coupon': coupon,
		'discount': discount,
		'total': max(subtotal - discount, Decimal('0.00')),
	})


@require_POST
def apply_coupon(request):
	lines, subtotal = _cart_lines(request)
	code = request.POST.get('coupon_code', '').strip().upper()
	coupon = Coupon.objects.filter(code=code).first()
	if not lines:
		messages.error(request, 'Ajoutez un article au panier avant d’utiliser un code promo.')
	elif not coupon or not coupon.is_valid_for(subtotal):
		request.session.pop('coupon_code', None)
		messages.error(request, 'Ce code promo est invalide, expiré ou ne répond pas au montant minimum.')
	else:
		request.session['coupon_code'] = coupon.code
		messages.success(request, 'Code promo appliqué à votre panier.')
	return redirect('panier')


@require_POST
def remove_coupon(request):
	request.session.pop('coupon_code', None)
	messages.info(request, 'Code promo retiré.')
	return redirect('panier')


@require_POST
def add_to_cart(request):
	product = get_object_or_404(Product, pk=request.POST.get('product_id'), is_active=True)
	size = request.POST.get('size', '').strip().upper()
	try:
		quantity = int(request.POST.get('quantity', '1'))
	except ValueError:
		quantity = 0
	if quantity < 1 or quantity > product.stock:
		return JsonResponse({'error': 'La quantité demandée n’est pas disponible.'}, status=400)
	if product.sizes and size not in product.sizes:
		return JsonResponse({'error': 'Choisissez une taille disponible.'}, status=400)
	key = f'{product.pk}:{size}'
	cart = request.session.get('cart', {})
	current_quantity = cart.get(key, {}).get('quantity', 0)
	if current_quantity + quantity > product.stock:
		return JsonResponse({'error': 'Le stock ne permet pas cette quantité.'}, status=400)
	cart[key] = {'product_id': product.pk, 'quantity': current_quantity + quantity, 'size': size}
	request.session['cart'] = cart
	return JsonResponse(_cart_json(request))


@require_POST
def update_cart(request):
	key = request.POST.get('key', '')
	cart = request.session.get('cart', {})
	entry = cart.get(key)
	if not entry:
		return JsonResponse({'error': 'Cet article ne se trouve plus dans le panier.'}, status=404)
	if request.POST.get('action') == 'remove':
		cart.pop(key, None)
	else:
		try:
			quantity = int(request.POST.get('quantity', '1'))
		except ValueError:
			quantity = 0
		product = Product.objects.filter(pk=entry.get('product_id'), is_active=True).first()
		if not product or quantity < 1 or quantity > product.stock:
			return JsonResponse({'error': 'La quantité demandée n’est pas disponible.'}, status=400)
		entry['quantity'] = quantity
		cart[key] = entry
	request.session['cart'] = cart
	return JsonResponse(_cart_json(request))


@login_required
def checkout(request):
	lines, subtotal = _cart_lines(request)
	coupon = Coupon.objects.filter(code=request.session.get('coupon_code', '')).first()
	discount = coupon.calculate_discount(subtotal) if coupon and coupon.is_valid_for(subtotal) else Decimal('0.00')
	if not discount:
		coupon = None
	use_loyalty_points = request.method == 'POST' and request.POST.get('use_loyalty_points') == '1'
	available_points = request.user.loyalty_points
	preview_points_used = min(available_points, int(max(subtotal - discount, Decimal('0.00')) // Decimal('10'))) if use_loyalty_points else 0
	preview_loyalty_discount = Decimal(preview_points_used * 10)
	checkout_context = {
		'lines': lines,
		'subtotal': subtotal,
		'coupon': coupon,
		'discount': discount,
		'loyalty_points_available': available_points,
		'loyalty_points_used': preview_points_used,
		'loyalty_discount': preview_loyalty_discount,
		'use_loyalty_points': use_loyalty_points,
		'total': max(subtotal - discount - preview_loyalty_discount, Decimal('0.00')),
	}
	if not lines:
		messages.info(request, 'Votre panier est vide.')
		return redirect('catalogue')
	if request.method == 'POST':
		if any(not request.POST.get(field, '').strip() for field in ('phone', 'address', 'city')):
			messages.error(request, 'Renseignez votre téléphone et votre adresse de livraison.')
			return render(request, 'boutique/checkout_payment.html', checkout_context)
		payment_method = request.POST.get('payment_method')
		if payment_method not in Order.PaymentMethod.values:
			messages.error(request, 'Choisissez Wave maintenant ou le paiement à la livraison.')
			return render(request, 'boutique/checkout_payment.html', checkout_context)
		with transaction.atomic():
			coupon_code = request.session.get('coupon_code', '')
			coupon = Coupon.objects.select_for_update().filter(code=coupon_code).first() if coupon_code else None
			if coupon_code and (not coupon or not coupon.is_valid_for(subtotal)):
				request.session.pop('coupon_code', None)
				messages.error(request, 'Le code promo n’est plus valide. Vérifiez le nouveau total de votre panier.')
				checkout_context.update({'coupon': None, 'discount': Decimal('0.00'), 'total': subtotal})
				return render(request, 'boutique/checkout_payment.html', checkout_context)
			coupon_discount = coupon.calculate_discount(subtotal) if coupon else Decimal('0.00')
			locked_user = get_user_model().objects.select_for_update().get(pk=request.user.pk)
			remaining_for_points = max(subtotal - coupon_discount, Decimal('0.00'))
			loyalty_points_used = min(
				locked_user.loyalty_points,
				int(remaining_for_points // Decimal('10')),
			) if use_loyalty_points else 0
			loyalty_discount = Decimal(loyalty_points_used * 10)
			discount = coupon_discount + loyalty_discount
			current_products = {}
			requested_quantities = {}
			for line in lines:
				product_id = line['product'].pk
				requested_quantities[product_id] = requested_quantities.get(product_id, 0) + line['quantity']
			for product_id, quantity in requested_quantities.items():
				product = Product.objects.select_for_update().filter(pk=product_id, is_active=True).first()
				if not product or product.stock < quantity:
					messages.error(request, f'Le stock de « {line["product"].name} » vient de changer. Vérifiez votre panier.')
					return redirect('panier')
				current_products[product_id] = product
			order = Order.objects.create(
				user=locked_user,
				payment_method=payment_method,
				payment_status=(
					Order.PaymentStatus.PENDING
					if payment_method == Order.PaymentMethod.WAVE
					else Order.PaymentStatus.DUE_ON_DELIVERY
				),
				status=(
					Order.Status.AWAITING_PAYMENT
					if payment_method == Order.PaymentMethod.WAVE
					else Order.Status.CONFIRMED
				),
				customer_name=f'{request.user.first_name} {request.user.last_name}'.strip() or request.user.email,
				customer_email=request.user.email,
				phone=request.POST['phone'].strip(),
				address=request.POST['address'].strip(),
				city=request.POST['city'].strip(),
				delivery_note=request.POST.get('delivery_note', '').strip(),
				subtotal=subtotal,
				discount_amount=discount,
				loyalty_points_used=loyalty_points_used,
				coupon=coupon,
			)
			for line in lines:
				product = current_products[line['product'].pk]
				OrderItem.objects.create(
					order=order,
					product=product,
					product_name=product.name,
					size=line['size'],
					quantity=line['quantity'],
					unit_price=product.price,
				)
			for product_id, quantity in requested_quantities.items():
				product = current_products[product_id]
				product.stock -= quantity
				product.save(update_fields=['stock', 'updated_at'])
			if coupon:
				coupon.uses_count += 1
				coupon.save(update_fields=['uses_count'])
			if loyalty_points_used:
				locked_user.loyalty_points -= loyalty_points_used
				locked_user.save(update_fields=['loyalty_points'])
		wave_launch_url = ''
		if payment_method == Order.PaymentMethod.WAVE and (settings.WAVE_API_KEY or settings.WAVE_PAYMENT_LINK_BASE):
			try:
				if settings.WAVE_API_KEY:
					wave_session = create_checkout_session(order)
					order.wave_checkout_id = wave_session['id']
					order.wave_launch_url = wave_session['wave_launch_url']
					order.save(update_fields=['wave_checkout_id', 'wave_launch_url'])
				else:
					order.wave_launch_url = create_merchant_payment_link(settings.WAVE_PAYMENT_LINK_BASE, order.total)
					order.save(update_fields=['wave_launch_url'])
				wave_launch_url = order.wave_launch_url
			except WaveCheckoutError:
				logger.exception('Could not create Wave checkout for order %s', order.reference)
				messages.warning(request, 'Le paiement Wave dans l’application est momentanément indisponible. Les instructions de transfert restent affichées.')
		email_sent = send_order_received_email(order, request=request)
		store_email_sent = send_new_order_notification_email(order, request=request)
		if not store_email_sent:
			logger.warning('Order %s was created but the store notification email failed', order.reference)
		if email_sent and 'console.EmailBackend' not in settings.EMAIL_BACKEND:
			messages.success(request, f'Commande reçue. Un e-mail de confirmation vient d’être envoyé à {order.customer_email}.')
		elif email_sent:
			messages.info(request, 'Commande reçue. En mode local, l’e-mail de confirmation est affiché dans le terminal et n’est pas envoyé au client.')
		else:
			messages.warning(request, 'Commande reçue, mais l’e-mail n’a pas pu être envoyé. Votre commande reste enregistrée.')
		request.session['cart'] = {}
		request.session.pop('coupon_code', None)
		return redirect('commande_detail', reference=order.reference)
	return render(request, 'boutique/checkout_payment.html', {
		**checkout_context,
		'use_loyalty_points': False,
		'loyalty_points_used': 0,
		'loyalty_discount': Decimal('0.00'),
		'total': subtotal - discount,
	})


@login_required
def order_history(request):
	orders = Order.objects.filter(user=request.user).prefetch_related('items')
	return render(request, 'boutique/order_history.html', {'orders': orders})


@login_required
def order_detail(request, reference):
	order = get_object_or_404(Order.objects.prefetch_related('items'), reference=reference)
	if order.user_id != request.user.id and not request.user.is_superuser:
		raise Http404
	return_request = ReturnRequest.objects.filter(order=order).first()
	context = {
		'order': order,
		'return_request': return_request,
		'return_form': ReturnRequestForm(),
		'return_eligible': (
			order.status == Order.Status.DELIVERED
			and order.payment_status == Order.PaymentStatus.CONFIRMED
			and order.delivered_at is not None
			and timezone.now() - order.delivered_at <= timedelta(days=14)
			and return_request is None
		),
	}
	if order.payment_method == Order.PaymentMethod.CASH_ON_DELIVERY:
		return render(request, 'boutique/order_detail_delivery.html', context)
	if order.payment_method == Order.PaymentMethod.WAVE:
		return render(request, 'boutique/order_detail_wave_checkout.html', context)
	return render(request, 'boutique/order_detail.html', context)


@login_required
def receipt(request, reference):
	order = get_object_or_404(Order.objects.prefetch_related('items'), reference=reference)
	if order.user_id != request.user.id and not request.user.is_superuser:
		raise Http404
	if order.payment_status != Order.PaymentStatus.CONFIRMED:
		return HttpResponse('Le reçu sera disponible après confirmation du paiement.', status=409)
	return render(request, 'boutique/receipt.html', {'order': order})


@login_required
@require_POST
def request_return(request, reference):
	order = get_object_or_404(Order.objects.select_related('user'), reference=reference)
	if order.user_id != request.user.id:
		raise Http404
	if order.status != Order.Status.DELIVERED or order.payment_status != Order.PaymentStatus.CONFIRMED:
		messages.error(request, 'Une demande de retour est possible après livraison et confirmation du paiement.')
		return redirect('commande_detail', reference=reference)
	if not order.delivered_at or timezone.now() - order.delivered_at > timedelta(days=14):
		messages.error(request, 'Le délai de retour de 14 jours après livraison est dépassé.')
		return redirect('commande_detail', reference=reference)
	if hasattr(order, 'return_request'):
		messages.info(request, 'Une demande de retour existe déjà pour cette commande.')
		return redirect('commande_detail', reference=reference)
	form = ReturnRequestForm(request.POST)
	if form.is_valid():
		return_request = form.save(commit=False)
		return_request.order = order
		return_request.save()
		if send_return_request_notifications(return_request, request=request):
			messages.success(request, 'Votre demande de retour a été enregistrée. Vous et la boutique recevrez un e-mail de suivi.')
		else:
			messages.warning(request, 'Votre demande de retour est enregistrée, mais un e-mail n’a pas pu être envoyé.')
	else:
		messages.error(request, 'Indiquez brièvement la raison de votre demande.')
	return redirect('commande_detail', reference=reference)


def register(request):
	if request.user.is_authenticated:
		return redirect('accueil')
	form = RegistrationForm(request.POST or None)
	if request.method == 'POST' and form.is_valid():
		user = form.save()
		login(request, user)
		messages.success(request, 'Votre compte Yorwani est prêt.')
		return redirect('accueil')
	return render(request, 'boutique/register.html', {'form': form})


def login_view(request):
	if request.user.is_authenticated:
		return redirect('dashboard' if request.user.is_superuser else 'accueil')
	form = EmailAuthenticationForm(request, data=request.POST or None)
	if request.method == 'POST' and form.is_valid():
		user = form.get_user()
		login(request, user)
		if user.is_superuser:
			return redirect('dashboard')
		return redirect(request.GET.get('next') or 'accueil')
	return render(request, 'boutique/login.html', {'form': form})


@require_POST
def logout_view(request):
	logout(request)
	messages.success(request, 'Vous êtes bien déconnecté(e).', extra_tags='gold')
	return redirect('accueil')


admin_required = user_passes_test(lambda user: user.is_authenticated and user.is_superuser, login_url='connexion')


@admin_required
def dashboard(request):
	context = {
		'product_count': Product.objects.count(),
		'order_count': Order.objects.count(),
		'pending_count': Order.objects.filter(payment_status=Order.PaymentStatus.PENDING).count(),
		'recent_orders': Order.objects.select_related('user')[:6],
	}
	return render(request, 'boutique/admin_dashboard.html', context)


@admin_required
def admin_accounting(request):
	active_orders = Order.objects.exclude(status=Order.Status.CANCELLED)
	paid_orders = active_orders.filter(payment_status=Order.PaymentStatus.CONFIRMED)
	wave_pending_orders = active_orders.filter(
		payment_method=Order.PaymentMethod.WAVE,
		payment_status=Order.PaymentStatus.PENDING,
	)
	cash_due_orders = active_orders.filter(
		payment_method=Order.PaymentMethod.CASH_ON_DELIVERY,
		payment_status=Order.PaymentStatus.DUE_ON_DELIVERY,
	)
	open_orders = (wave_pending_orders | cash_due_orders).select_related('user').order_by('-created_at')

	def total_for(orders):
		totals = orders.aggregate(
			subtotal=Sum('subtotal'),
			shipping=Sum('shipping_fee'),
			discounts=Sum('discount_amount'),
		)
		return max(
			(totals['subtotal'] or Decimal('0.00'))
			+ (totals['shipping'] or Decimal('0.00'))
			- (totals['discounts'] or Decimal('0.00')),
			Decimal('0.00'),
		)

	context = {
		'received_total': total_for(paid_orders),
		'wave_pending_total': total_for(wave_pending_orders),
		'cash_due_total': total_for(cash_due_orders),
		'not_received_total': total_for(open_orders),
		'received_count': paid_orders.count(),
		'open_orders': open_orders,
	}
	return render(request, 'boutique/admin_accounting.html', context)


@admin_required
def admin_accounting_export(request):
	orders = Order.objects.exclude(status=Order.Status.CANCELLED).select_related('coupon').order_by('-created_at')
	response = HttpResponse(content_type='text/csv; charset=utf-8')
	response['Content-Disposition'] = 'attachment; filename="yorwani-comptabilite.csv"'
	response.write('\ufeff')
	writer = csv.writer(response)
	writer.writerow((
		'Référence', 'Date', 'Client', 'E-mail', 'Mode de paiement', 'État du paiement',
		'État commande', 'Sous-total FCFA', 'Livraison FCFA', 'Remise FCFA', 'Net FCFA', 'Code promo',
		'Transporteur', 'Numéro de suivi',
	))
	for order in orders:
		writer.writerow((
			order.reference,
			order.created_at.strftime('%Y-%m-%d %H:%M'),
			order.customer_name,
			order.customer_email,
			order.get_payment_method_display(),
			order.get_payment_status_display(),
			order.get_status_display(),
			order.subtotal,
			order.shipping_fee,
			order.discount_amount,
			order.total,
			order.coupon.code if order.coupon else '',
			order.shipping_carrier,
			order.tracking_number,
		))
	return response


@admin_required
def admin_products(request):
	products = Product.objects.select_related('category').order_by('-updated_at')
	return render(request, 'boutique/admin_products.html', {'products': products})


def _product_form_view(request, product=None):
	form = ProductForm(request.POST or None, request.FILES or None, instance=product)
	if request.method == 'POST' and form.is_valid():
		product = form.save()
		for index, image_file in enumerate(request.FILES.getlist('gallery')):
			ProductImage.objects.create(product=product, image=image_file, position=index)
		messages.success(request, f'« {product.name} » a été enregistré.')
		return redirect('admin_articles')
	return render(request, 'boutique/admin_product_form.html', {'form': form, 'product': product})


@admin_required
def product_create(request):
	return _product_form_view(request)


@admin_required
def product_edit(request, product_id):
	return _product_form_view(request, get_object_or_404(Product, pk=product_id))


@admin_required
@require_POST
def product_delete(request, product_id):
	product = get_object_or_404(Product, pk=product_id)
	name = product.name
	product.delete()
	messages.success(request, f'« {name} » a été supprimé du catalogue.')
	return redirect('admin_articles')


@admin_required
@require_POST
def product_image_delete(request, image_id):
	image = get_object_or_404(ProductImage, pk=image_id)
	product_id = image.product_id
	if image.image:
		image.image.delete(save=False)
	image.delete()
	messages.success(request, 'Image supprimée.')
	return redirect('admin_article_modifier', product_id=product_id)


@admin_required
def admin_orders(request):
	orders = Order.objects.select_related('user').prefetch_related('items')
	return render(request, 'boutique/admin_orders.html', {'orders': orders})


@admin_required
def admin_order_detail(request, reference):
	order = get_object_or_404(Order.objects.select_related('user').prefetch_related('items'), reference=reference)
	context = {
		'order': order,
		'fulfillment_form': FulfillmentForm(instance=order),
		'return_request': ReturnRequest.objects.filter(order=order).first(),
	}
	if order.payment_method == Order.PaymentMethod.CASH_ON_DELIVERY:
		return render(request, 'boutique/admin_order_delivery_detail.html', context)
	return render(request, 'boutique/admin_order_detail.html', context)


@admin_required
@require_POST
def update_fulfillment(request, reference):
	order = get_object_or_404(Order, reference=reference)
	previous_status = order.status
	form = FulfillmentForm(request.POST, instance=order)
	if not form.is_valid():
		messages.error(request, 'Vérifiez l’état de la commande et les informations de suivi.')
		return redirect('admin_commande_detail', reference=reference)
	new_status = form.cleaned_data['status']
	status_order = {
		Order.Status.CONFIRMED: 0,
		Order.Status.PREPARING: 1,
		Order.Status.SHIPPED: 2,
		Order.Status.DELIVERED: 3,
	}
	if (
		order.status in status_order
		and new_status in status_order
		and status_order[new_status] < status_order[order.status]
	):
		messages.error(request, 'L’état d’expédition ne peut pas revenir à une étape précédente.')
		return redirect('admin_commande_detail', reference=reference)
	if new_status in (Order.Status.PREPARING, Order.Status.SHIPPED, Order.Status.DELIVERED):
		cod_order = order.payment_method == Order.PaymentMethod.CASH_ON_DELIVERY and order.payment_status == Order.PaymentStatus.DUE_ON_DELIVERY
		if order.payment_status != Order.PaymentStatus.CONFIRMED and not cod_order:
			messages.error(request, 'Confirmez le paiement avant de préparer ou d’expédier cette commande.')
			return redirect('admin_commande_detail', reference=reference)
		if new_status == Order.Status.DELIVERED and order.payment_status != Order.PaymentStatus.CONFIRMED:
			messages.error(request, 'Confirmez la réception du paiement à la livraison avant de marquer la commande livrée.')
			return redirect('admin_commande_detail', reference=reference)
	if new_status == Order.Status.CONFIRMED and order.payment_status != Order.PaymentStatus.CONFIRMED:
		messages.error(request, 'Confirmez le paiement avant de confirmer la commande.')
		return redirect('admin_commande_detail', reference=reference)
	updated_order = form.save(commit=False)
	if new_status == Order.Status.DELIVERED and not updated_order.delivered_at:
		updated_order.delivered_at = timezone.now()
		if not updated_order.loyalty_points_awarded and updated_order.total >= Decimal('1000.00'):
			customer = type(updated_order.user).objects.select_for_update().get(pk=updated_order.user_id)
			updated_order.loyalty_points_awarded = int(updated_order.total // Decimal('1000'))
			customer.loyalty_points += updated_order.loyalty_points_awarded
			customer.save(update_fields=['loyalty_points'])
	updated_order.save()
	if previous_status != updated_order.status:
		if not send_order_status_email(updated_order, request=request):
			messages.warning(request, 'État mis à jour, mais l’e-mail client n’a pas pu être envoyé.')
		else:
			messages.success(request, 'État de la commande mis à jour et client averti par e-mail.')
	else:
		messages.success(request, 'Informations de suivi enregistrées.')
	return redirect('admin_commande_detail', reference=reference)


@admin_required
@require_POST
def send_wave_payment_link(request, reference):
	order = get_object_or_404(Order, reference=reference)
	if order.payment_method != Order.PaymentMethod.WAVE:
		messages.error(request, 'Cette commande n’utilise pas Wave comme mode de paiement.')
	elif order.payment_status == Order.PaymentStatus.CONFIRMED or order.status == Order.Status.CANCELLED:
		messages.error(request, 'Cette commande ne peut plus recevoir de lien de paiement.')
	else:
		payment_url = request.POST.get('wave_payment_url', '').strip()
		parsed_url = urlparse(payment_url)
		if parsed_url.scheme != 'https' or parsed_url.hostname != 'pay.wave.com' or not parsed_url.path.startswith(('/m/', '/c/')):
			messages.error(request, 'Collez un lien Wave sécurisé commençant par https://pay.wave.com/.')
		else:
			order.wave_launch_url = payment_url
			order.save(update_fields=['wave_launch_url'])
			if send_wave_payment_link_email(order, request=request):
				messages.success(request, f'Le lien de paiement Wave a été envoyé à {order.customer_email}.')
			else:
				messages.warning(request, 'Le lien est enregistré, mais le courriel n’a pas pu être envoyé.')
	return redirect('admin_commande_detail', reference=reference)


@admin_required
@require_POST
def confirm_payment(request, reference):
	order = get_object_or_404(Order, reference=reference)
	if order.status == Order.Status.CANCELLED:
		messages.error(request, 'Une commande annulée ne peut pas être confirmée.')
	elif order.payment_status == Order.PaymentStatus.CONFIRMED:
		messages.info(request, 'Ce paiement est déjà confirmé.')
	elif order.payment_method == Order.PaymentMethod.WAVE and order.payment_status == Order.PaymentStatus.PENDING:
		order.payment_status = Order.PaymentStatus.CONFIRMED
		order.status = Order.Status.CONFIRMED
		order.save(update_fields=['payment_status', 'status'])
		email_sent = send_payment_confirmed_email(order)
		if email_sent and 'console.EmailBackend' not in settings.EMAIL_BACKEND:
			messages.success(request, f'Paiement Wave confirmé. Un e-mail a été envoyé à {order.customer_email}.')
		elif email_sent:
			messages.success(request, 'Paiement Wave confirmé. L’e-mail apparaît dans le terminal local ; le reçu est disponible.')
		else:
			messages.warning(request, 'Paiement confirmé, mais l’e-mail client n’a pas pu être envoyé. Le reçu est disponible.')
	elif order.payment_method == Order.PaymentMethod.CASH_ON_DELIVERY and order.payment_status == Order.PaymentStatus.DUE_ON_DELIVERY:
		order.payment_status = Order.PaymentStatus.CONFIRMED
		order.status = Order.Status.CONFIRMED
		order.save(update_fields=['payment_status', 'status'])
		email_sent = send_payment_confirmed_email(order)
		if email_sent and 'console.EmailBackend' not in settings.EMAIL_BACKEND:
			messages.success(request, f'Paiement à la livraison confirmé. Un e-mail a été envoyé à {order.customer_email}.')
		elif email_sent:
			messages.success(request, 'Paiement à la livraison confirmé. L’e-mail apparaît dans le terminal local ; le reçu est disponible.')
		else:
			messages.warning(request, 'Paiement confirmé, mais l’e-mail client n’a pas pu être envoyé. Le reçu est disponible.')
	else:
		messages.error(request, 'Le statut du paiement ne correspond pas au mode de règlement de cette commande.')
	return redirect('admin_commande_detail', reference=reference)


def _valid_wave_webhook_signature(secret, signature_header, raw_body):
	parts = {}
	for item in signature_header.split(','):
		key, separator, value = item.strip().partition('=')
		if separator:
			parts.setdefault(key, []).append(value)
	try:
		timestamp = int(parts['t'][0])
		current_time = time.time()
	except (KeyError, IndexError, ValueError):
		return False
	if timestamp < current_time - 300 or timestamp > current_time + 30:
		return False
	expected = hmac.new(secret.encode(), str(timestamp).encode() + raw_body, hashlib.sha256).hexdigest()
	return any(hmac.compare_digest(expected, value) for value in parts.get('v1', []))


@csrf_exempt
@require_POST
def wave_webhook(request):
	secret = settings.WAVE_WEBHOOK_SECRET
	if not secret:
		return HttpResponse(status=503)
	if not _valid_wave_webhook_signature(secret, request.headers.get('Wave-Signature', ''), request.body):
		return HttpResponse(status=401)
	try:
		event = json.loads(request.body)
	except (json.JSONDecodeError, UnicodeDecodeError):
		return HttpResponse(status=400)
	if event.get('type') != 'checkout.session.completed':
		return JsonResponse({'received': True})

	data = event.get('data') or {}
	if data.get('payment_status') != 'succeeded' or data.get('checkout_status') != 'complete':
		return JsonResponse({'received': True})
	try:
		paid_amount = Decimal(str(data.get('amount')))
	except Exception:
		return HttpResponse(status=400)
	if data.get('currency') != 'XOF':
		return HttpResponse(status=400)

	with transaction.atomic():
		order = Order.objects.select_for_update().filter(wave_checkout_id=data.get('id')).first()
		if not order:
			return JsonResponse({'received': True})
		if paid_amount != order.total or order.payment_method != Order.PaymentMethod.WAVE:
			return HttpResponse(status=400)
		if order.payment_status == Order.PaymentStatus.CONFIRMED:
			return JsonResponse({'received': True})
		if order.payment_status != Order.PaymentStatus.PENDING or order.status == Order.Status.CANCELLED:
			return HttpResponse(status=409)
		order.payment_status = Order.PaymentStatus.CONFIRMED
		order.status = Order.Status.CONFIRMED
		order.wave_transaction_id = str(data.get('transaction_id', ''))
		order.save(update_fields=['payment_status', 'status', 'wave_transaction_id'])

	if not send_payment_confirmed_email(order):
		logger.error('Payment confirmed by Wave but customer email failed for order %s', order.reference)
	return JsonResponse({'received': True})
