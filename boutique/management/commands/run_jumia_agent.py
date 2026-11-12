from django.core.management.base import BaseCommand, CommandError
from django.db import connections

from boutique.agents.worker import run_worker


class Command(BaseCommand):
    help = "Lance le worker Jumia en lecture seule, mode simulation uniquement."

    def add_arguments(self, parser):
        parser.add_argument("--once", action="store_true", help="Traite au plus une tache puis quitte.")
        parser.add_argument("--limit", type=int, help="Nombre maximum de taches a traiter.")
        parser.add_argument("--poll-seconds", type=int, help="Intervalle d'attente entre verifications.")

    def handle(self, *args, **options):
        if options["limit"] is not None and options["limit"] < 1:
            raise CommandError("--limit doit etre superieur a zero.")
        try:
            count = run_worker(
                once=options["once"],
                limit=options["limit"],
                poll_seconds=options["poll_seconds"],
            )
        finally:
            connections.close_all()
        self.stdout.write(self.style.SUCCESS(f"{count} tache(s) inspectee(s) en lecture seule."))
