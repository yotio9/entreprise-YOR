from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from boutique.services.product_manager import (
	ProductManagerError,
	apply_product_change_plan,
	build_product_change_plan,
	build_search_results,
	format_apply_result,
	format_change_history,
	format_product_plan,
	format_search_results,
	get_change_history,
)


class Command(BaseCommand):
	help = 'Recherche et modifie le catalogue avec aperçu approuvé et journal d’audit.'

	def add_arguments(self, parser):
		parser.add_argument(
			'--action',
			choices=('search', 'history', 'set-price', 'set-stock', 'update', 'update-csv', 'publish', 'unpublish', 'prepare-collection'),
			required=True,
		)
		parser.add_argument('--mode', choices=('preview', 'apply'), default='preview')
		parser.add_argument('--product-id', type=int)
		parser.add_argument('--sku')
		parser.add_argument('--collection', help='Collection à cibler ou modifiant de prix par pourcentage.')
		parser.add_argument('--target-collection', help='Nouvelle collection pour --action update.')
		parser.add_argument('--price', help='Prix fixe, jusqu’à deux décimales.')
		parser.add_argument('--percent', help='Pourcentage positif ou négatif pour toute une collection.')
		parser.add_argument('--stock', help='Nouvelle quantité en stock.')
		parser.add_argument('--name')
		parser.add_argument('--description')
		parser.add_argument('--image-filename')
		parser.add_argument('--images', help='Dossier local contenant les images du CSV ou --image-filename.')
		parser.add_argument('--csv', help='CSV UTF-8 pour les modifications ou la sélection.')
		parser.add_argument('--query', help='Recherche dans les noms, descriptions et SKU.')
		parser.add_argument('--missing-sku', action='store_true')
		parser.add_argument('--out-of-stock', action='store_true')
		parser.add_argument('--low-stock-threshold', type=int)
		parser.add_argument('--visibility', choices=('all', 'published', 'draft'), default='all')
		parser.add_argument('--fingerprint', help='Empreinte d’un lot pour consulter son journal.')
		parser.add_argument('--confirm-digest', help='Empreinte affichée par PREVIEW; obligatoire pour APPLY.')
		parser.add_argument('--allow-remote-database', action='store_true', help='Autorise explicitement une base distante.')
		parser.add_argument('--allow-production', action='store_true', help='Autorise explicitement une configuration DEBUG=0.')

	def _guard_database(self, options):
		if not settings.DEBUG and not options['allow_production']:
			raise CommandError('DEBUG=0 : opération refusée sans --allow-production.')
		engine = settings.DATABASES['default']['ENGINE']
		host = (settings.DATABASES['default'].get('HOST') or '').strip().lower()
		local_hosts = {'', 'localhost', '127.0.0.1', '::1'}
		if not (engine.endswith('sqlite3') or host in local_hosts) and not options['allow_remote_database']:
			raise CommandError('Base distante détectée; utilisez --allow-remote-database explicitement.')

	def handle(self, *args, **options):
		self._guard_database(options)
		action = options['action']
		if action == 'search':
			if options['mode'] != 'preview':
				raise CommandError('search est une consultation en lecture seule; utilisez --mode preview.')
			if options['low_stock_threshold'] is not None and options['low_stock_threshold'] < 0:
				raise CommandError('--low-stock-threshold doit être positif ou nul.')
			results = build_search_results({
				'query': options['query'],
				'collection': options['collection'],
				'missing_sku': options['missing_sku'],
				'out_of_stock': options['out_of_stock'],
				'low_stock': options['low_stock_threshold'],
				'visibility': options['visibility'],
			})
			self.stdout.write(format_search_results(results))
			return
		if action == 'history':
			if options['mode'] != 'preview':
				raise CommandError('history est une consultation en lecture seule; utilisez --mode preview.')
			if not options['fingerprint']:
				raise CommandError('history exige --fingerprint.')
			try:
				batch, changes = get_change_history(options['fingerprint'])
			except ProductManagerError as exc:
				raise CommandError(str(exc)) from None
			self.stdout.write(format_change_history(batch, changes))
			return

		try:
			plan = build_product_change_plan(action, options)
		except ProductManagerError as exc:
			raise CommandError(str(exc)) from None
		if options['mode'] == 'preview':
			self.stdout.write(format_product_plan(plan))
			return

		approved = (options.get('confirm_digest') or '').strip().lower()
		if not approved:
			self.stdout.write(format_product_plan(plan))
			raise CommandError('APPLY exige --confirm-digest avec l’empreinte approuvée.')
		if approved != plan.fingerprint:
			self.stdout.write(format_product_plan(plan))
			raise CommandError('Les données ou le CSV ont changé depuis PREVIEW; aucun changement appliqué.')
		if plan.errors:
			self.stdout.write(format_product_plan(plan))
			raise CommandError('L’aperçu contient des erreurs ou conflits; corrigez-les avant APPLY.')
		try:
			result = apply_product_change_plan(plan)
		except ProductManagerError as exc:
			raise CommandError(f'{exc} Aucune modification en base n’a été validée.') from None
		except Exception as exc:
			raise CommandError(f'Échec ({type(exc).__name__}). Relancez PREVIEW et vérifiez l’état avant une nouvelle approbation.') from None
		self.stdout.write(format_apply_result(result))
