import logging

from django.conf import settings
from django.core.mail import send_mail
from django.template.loader import render_to_string
from django.urls import reverse

logger = logging.getLogger(__name__)


def _send_order_email(order, subject, template_name, request=None):
    try:
        context = {
            'order': order,
            'wave_merchant_number': settings.WAVE_MERCHANT_NUMBER,
        }
        if request is not None:
            context.update({
                'store_url': request.build_absolute_uri(reverse('accueil')),
                'order_url': request.build_absolute_uri(reverse('commande_detail', args=[order.reference])),
                'orders_url': request.build_absolute_uri(reverse('historique_commandes')),
            })
        body = render_to_string(template_name, context)
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


def send_order_received_email(order, request=None):
    return _send_order_email(
        order,
        f'Yorwani | Commande reçue {order.reference}',
        'boutique/emails/order_received.txt',
        request=request,
    )


def send_new_order_notification_email(order, request=None):
    recipient = getattr(settings, 'ORDER_NOTIFICATION_EMAIL', '')
    if not recipient:
        logger.warning('No store order notification address configured for %s', order.reference)
        return False

    try:
        context = {'order': order}
        if request is not None:
            context.update({
                'admin_order_url': request.build_absolute_uri(reverse('admin_commande_detail', args=[order.reference])),
                'admin_orders_url': request.build_absolute_uri(reverse('admin_commandes')),
            })
        body = render_to_string('boutique/emails/new_order_notification.txt', context)
        return bool(send_mail(
            subject=f'Yorwani | Nouvelle commande {order.reference}',
            message=body,
            from_email=settings.DEFAULT_FROM_EMAIL,
            recipient_list=[recipient],
            fail_silently=False,
        ))
    except Exception:
        logger.exception('Could not send store order notification for %s', order.reference)
        return False


def send_wave_payment_link_email(order, request=None):
    return _send_order_email(
        order,
        f'Yorwani | Lien de paiement Wave {order.reference}',
        'boutique/emails/wave_payment_link.txt',
        request=request,
    )


def send_payment_confirmed_email(order):
    return _send_order_email(
        order,
        f'Yorwani | Paiement confirmé {order.reference}',
        'boutique/emails/payment_confirmed.txt',
    )


def send_order_status_email(order, request=None):
    return _send_order_email(
        order,
        f'Yorwani | {order.get_status_display()} · {order.reference}',
        'boutique/emails/order_status.txt',
        request=request,
    )


def send_return_request_notifications(return_request, request=None):
    order = return_request.order
    admin_recipient = getattr(settings, 'ORDER_NOTIFICATION_EMAIL', '')
    admin_context = {'return_request': return_request, 'order': order}
    if request is not None:
        admin_context['admin_return_url'] = request.build_absolute_uri(reverse('owner_admin:boutique_returnrequest_change', args=[return_request.pk]))
    try:
        admin_sent = bool(admin_recipient) and bool(send_mail(
            subject=f'Yorwani | Demande de retour {order.reference}',
            message=render_to_string('boutique/emails/return_requested_store.txt', admin_context),
            from_email=settings.DEFAULT_FROM_EMAIL,
            recipient_list=[admin_recipient],
            fail_silently=False,
        ))
        customer_sent = bool(send_mail(
            subject=f'Yorwani | Demande de retour reçue {order.reference}',
            message=render_to_string('boutique/emails/return_status.txt', {'return_request': return_request, 'order': order}),
            from_email=settings.DEFAULT_FROM_EMAIL,
            recipient_list=[order.customer_email],
            fail_silently=False,
        ))
        return admin_sent and customer_sent
    except Exception:
        logger.exception('Could not send return-request emails for %s', order.reference)
        return False


def send_return_status_email(return_request):
    order = return_request.order
    try:
        return bool(send_mail(
            subject=f'Yorwani | Retour {return_request.get_status_display()} · {order.reference}',
            message=render_to_string('boutique/emails/return_status.txt', {'return_request': return_request, 'order': order}),
            from_email=settings.DEFAULT_FROM_EMAIL,
            recipient_list=[order.customer_email],
            fail_silently=False,
        ))
    except Exception:
        logger.exception('Could not send return status email for %s', order.reference)
        return False