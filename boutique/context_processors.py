from django.conf import settings


def store_settings(request):
    cart = request.session.get('cart', {})
    return {
        'WAVE_MERCHANT_NUMBER': settings.WAVE_MERCHANT_NUMBER,
        'WAVE_QR_IMAGE': settings.WAVE_QR_IMAGE,
        'BRAND_LOGO_URL': settings.BRAND_LOGO_URL,
        'cart_count': sum(item.get('quantity', 0) for item in cart.values()),
    }