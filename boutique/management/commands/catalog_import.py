from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from boutique.services.catalog_import import (
	CatalogImportError,
	apply_import_plan,
	build_import_plan,
	format_apply_report,
	format_preview,
)


class Command(BaseCommand):
	help = 'Prévisualise ou importe un catalogue CSV sans publier automatiquement les produits.'

	def add_arguments(self, parser):
		parser.add_argument('--mode', choices=('preview', 'apply'), required=True)
		parser.add_argument('--file', required=True, help='Chemin du fichier CSV UTF-8.')
		parser.add_argument('--images', required=True, help='Dossier contenant les images référencées.')
		parser.add_argument('--confirm-digest', help='Empreinte affichée par PREVIEW, obligatoire en mode APPLY.')
		parser.add_argument('--allow-remote-database', action='store_true', help='Autorise explicitement une base distante.')
		parser.add_argument('--allow-production', action='store_true', help='Autorise explicitement une configuration DEBUG=0.')

	def _guard_database(self, options):
		if not settings.DEBUG and not options['allow_production']:
			raise CommandError('DEBUG=0 : opération refusée sans --allow-production.')
		engine = settings.DATABASES['default']['ENGINE']
		host = (settings.DATABASES['default'].get('HOST') or '').strip().lower()
		local_hosts = {'', 'localhost', '127.0.0.1', '::1'}
		is_local_database = engine.endswith('sqlite3') or host in local_hosts
		if not is_local_database and not options['allow_remote_database']:
			raise CommandError('Base distante détectée; utilisez une base locale ou fournissez --allow-remote-database explicitement.')

	def handle(self, *args, **options):
		self._guard_database(options)
		try:
			plan = build_import_plan(Path(options['file']), Path(options['images']))
		except CatalogImportError as exc:
			raise CommandError(str(exc)) from None

		if options['mode'] == 'preview':
			self.stdout.write(format_preview(plan))
			return

		approved_digest = (options.get('confirm_digest') or '').strip().lower()
		if not approved_digest:
			raise CommandError('APPLY nécessite --confirm-digest avec l’empreinte issue de PREVIEW.')
		if approved_digest != plan.fingerprint:
			raise CommandError('L’empreinte diffère de PREVIEW; relancez PREVIEW et approuvez le nouvel aperçu.')
		try:
			report = apply_import_plan(plan, Path(options['images']))
		except Exception as exc:
			raise CommandError(f'Import interrompu ({type(exc).__name__}); relancez APPLY avec la même empreinte pour reprendre.') from None
		self.stdout.write(format_apply_report(report))
		if report['errors']:
			self.stdout.write(self.style.WARNING('Certaines lignes sont en erreur; corrigez la source puis prévisualisez un nouvel import.'))
			raise CommandError('Import partiel : consultez le rapport par ligne ci-dessus.')
		else:
			self.stdout.write(self.style.SUCCESS('Import terminé. Tous les nouveaux produits restent en brouillon.'))
