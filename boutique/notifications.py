import logging

from django.conf import settings
from django.core.mail import send_mail
from django.template.loader import render_to_string

logger = logging.getLogger(__name__)


def _send_order_email(order, subject, template_name):
    try:
        body = render_to_string(template_name, {
            'order': order,
            'wave_merchant_number': settings.WAVE_MERCHANT_NUMBER,
        })
        return bool(send_mail(
            subject=subject,
            message=body,
            from_email=settings.DEFAULT_FROM_EMAIL,
            recipient_list=[order.customer_email],
            fail_silently=False,
        ))
    except Exception:
        logger.exception('Could not send order email for %s', order.reference)
        return False


def send_order_received_email(order):
    return _send_order_email(
        order,
        f'Yorwani | Commande reçue {order.reference}',
        'boutique/emails/order_received.txt',
    )


def send_payment_confirmed_email(order):
    return _send_order_email(
        order,
        f'Yorwani | Paiement confirmé {order.reference}',
        'boutique/emails/payment_confirmed.txt',
    )