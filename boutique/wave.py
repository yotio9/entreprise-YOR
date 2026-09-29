import json
from decimal import Decimal
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse
from urllib.request import Request, urlopen

from django.conf import settings
from django.urls import reverse


class WaveCheckoutError(Exception):
	pass


def create_merchant_payment_link(base_url, amount):
	parsed_url = urlparse(base_url.strip())
	if (
		parsed_url.scheme != 'https'
		or parsed_url.hostname != 'pay.wave.com'
		or not parsed_url.path.startswith('/m/')
		or '/c/ci/' not in parsed_url.path
	):
		raise WaveCheckoutError('Configurez un lien marchand Wave valide pour la Côte d’Ivoire.')
	amount = Decimal(amount)
	if amount <= 0 or amount != amount.to_integral_value():
		raise WaveCheckoutError('Le montant de la commande doit être un nombre entier positif en XOF.')
	query = [(key, value) for key, value in parse_qsl(parsed_url.query, keep_blank_values=True) if key != 'amount']
	query.append(('amount', str(int(amount))))
	return urlunparse(parsed_url._replace(query=urlencode(query)))


def create_checkout_session(order):
	api_key = getattr(settings, 'WAVE_API_KEY', '')
	site_url = getattr(settings, 'WAVE_SITE_URL', '').rstrip('/')
	if not api_key:
		return None
	if not site_url.startswith('https://'):
		raise WaveCheckoutError('Configurez WAVE_SITE_URL avec le domaine HTTPS public du site.')
	if Decimal(order.total) != Decimal(order.total).to_integral_value():
		raise WaveCheckoutError('Le montant de la commande doit être un nombre entier en XOF.')

	order_url = f'{site_url}{reverse("commande_detail", args=[order.reference])}'
	payload = json.dumps({
		'amount': str(int(order.total)),
		'currency': 'XOF',
		'client_reference': order.reference,
		'success_url': f'{order_url}?wave=success',
		'error_url': f'{order_url}?wave=error',
	}).encode('utf-8')
	api_request = Request(
		'https://api.wave.com/v1/checkout/sessions',
		data=payload,
		headers={
			'Authorization': f'Bearer {api_key}',
			'Content-Type': 'application/json',
		},
		method='POST',
	)

	try:
		with urlopen(api_request, timeout=15) as response:
			session = json.loads(response.read())
	except (HTTPError, URLError, TimeoutError, json.JSONDecodeError) as error:
		raise WaveCheckoutError('Wave n’a pas pu créer la session de paiement.') from error

	checkout_id = session.get('id')
	launch_url = session.get('wave_launch_url')
	parsed_url = urlparse(launch_url or '')
	if not checkout_id or parsed_url.scheme != 'https' or parsed_url.hostname != 'pay.wave.com':
		raise WaveCheckoutError('Wave a retourné une session de paiement invalide.')
	return {'id': checkout_id, 'wave_launch_url': launch_url}