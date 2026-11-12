from django.core.management.base import BaseCommand, CommandError
from django.db import connection


class Command(BaseCommand):
    help = "V?rifie une connexion ? la base active sans afficher ses param?tres."

    def handle(self, *args, **options):
        try:
            connection.ensure_connection()
            with connection.cursor() as cursor:
                cursor.execute("SELECT 1")
                cursor.fetchone()
        except Exception as exc:
            raise CommandError(f"Base indisponible ({type(exc).__name__}).") from None
        self.stdout.write(self.style.SUCCESS(f"Base accessible ({connection.vendor})."))
