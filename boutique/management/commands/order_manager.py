from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from boutique.models import Order
from boutique.services.order_manager import (
    OrderManagerError,
    apply_order_change_plan,
    build_order_change_plan,
    export_orders_csv,
    format_detail,
    format_history,
    format_order_plan,
    format_search,
    format_statistics,
    get_order_change_history,
    order_detail,
    order_statistics,
    search_orders,
)


class Command(BaseCommand):
    help = "Consulte et gere les commandes avec apercu approuve et journal d'audit."

    def add_arguments(self, parser):
        parser.add_argument("--action", required=True, choices=("list", "detail", "stats", "export", "history", "transition", "cancel"))
        parser.add_argument("--reference", action="append", dest="references", help="Reference de commande; repetable.")
        parser.add_argument("--csv", help="CSV UTF-8 de selection, avec une colonne reference.")
        parser.add_argument("--query", help="Recherche dans reference, client, e-mail ou telephone.")
        parser.add_argument("--status", choices=[value for value, _ in Order.Status.choices], help="Filtre ou nouveau statut pour transition.")
        parser.add_argument("--payment-status", choices=[value for value, _ in Order.PaymentStatus.choices])
        parser.add_argument("--date-from", help="Date incluse, format YYYY-MM-DD.")
        parser.add_argument("--date-to", help="Date incluse, format YYYY-MM-DD.")
        parser.add_argument("--reason", help="Raison obligatoire pour annuler (5 a 500 caracteres).")
        parser.add_argument("--carrier", help="Transporteur lors du passage a shipped.")
        parser.add_argument("--tracking-number", help="Numero de suivi lors du passage a shipped.")
        parser.add_argument("--fingerprint", help="Empreinte du journal a consulter.")
        parser.add_argument("--mode", choices=("preview", "apply"), default="preview")
        parser.add_argument("--confirm-digest", help="Empreinte exacte affichee par PREVIEW.")
        parser.add_argument("--confirm-cancel", help="Doit valoir ANNULER pour appliquer une annulation.")
        parser.add_argument("--output", help="Fichier CSV de sortie; sinon CSV ecrit sur stdout.")
        parser.add_argument("--allow-remote-database", action="store_true", help="Autorise explicitement une base distante.")
        parser.add_argument("--allow-production", action="store_true", help="Autorise explicitement une configuration DEBUG=0.")

    def _guard_database(self, options):
        if not settings.DEBUG and not options["allow_production"]:
            raise CommandError("DEBUG=0 : operation refusee sans --allow-production.")
        engine = settings.DATABASES["default"]["ENGINE"]
        host = (settings.DATABASES["default"].get("HOST") or "").strip().lower()
        if not (engine.endswith("sqlite3") or host in {"", "localhost", "127.0.0.1", "::1"}) and not options["allow_remote_database"]:
            raise CommandError("Base distante detectee; utilisez --allow-remote-database explicitement.")

    def _query_options(self, options):
        return {
            "query": options["query"],
            "status": options["status"],
            "payment_status": options["payment_status"],
            "date_from": options["date_from"],
            "date_to": options["date_to"],
        }

    def handle(self, *args, **options):
        self._guard_database(options)
        action = options["action"]
        if action in ("list", "stats", "export"):
            query_options = self._query_options(options)
            try:
                if action == "list":
                    self.stdout.write(format_search(search_orders(query_options)))
                elif action == "stats":
                    self.stdout.write(format_statistics(order_statistics(query_options)))
                else:
                    result = export_orders_csv(query_options)
                    if options["output"]:
                        with Path(options["output"]).open("x", encoding="utf-8", newline="") as output_file:
                            output_file.write(result)
                        self.stdout.write(f"CSV exporte: {options['output']}")
                    else:
                        self.stdout.write(result, ending="")
            except OrderManagerError as exc:
                raise CommandError(str(exc)) from None
            except OSError as exc:
                raise CommandError(f"Export impossible ({type(exc).__name__}).") from None
            return
        if action == "detail":
            refs = options.get("references") or []
            if len(refs) != 1 or options.get("csv"):
                raise CommandError("detail exige exactement un --reference.")
            try:
                self.stdout.write(format_detail(order_detail(refs[0])))
            except OrderManagerError as exc:
                raise CommandError(str(exc)) from None
            return
        if action == "history":
            if options["mode"] != "preview" or not options["fingerprint"]:
                raise CommandError("history est en lecture seule et exige --fingerprint.")
            try:
                batch, rows = get_order_change_history(options["fingerprint"])
            except OrderManagerError as exc:
                raise CommandError(str(exc)) from None
            self.stdout.write(format_history(batch, rows))
            return
        if action == "transition" and not options["status"]:
            raise CommandError("transition exige --status.")
        try:
            plan = build_order_change_plan(action, {
                "references": options["references"],
                "csv": options["csv"],
                "target_status": options["status"],
                "reason": options["reason"],
                "carrier": options["carrier"],
                "tracking_number": options["tracking_number"],
            })
        except OrderManagerError as exc:
            raise CommandError(str(exc)) from None
        if options["mode"] == "preview":
            self.stdout.write(format_order_plan(plan))
            return
        digest = (options.get("confirm_digest") or "").strip().lower()
        if not digest:
            self.stdout.write(format_order_plan(plan))
            raise CommandError("APPLY exige --confirm-digest avec l'empreinte affichee.")
        if digest != plan.fingerprint:
            self.stdout.write(format_order_plan(plan))
            raise CommandError("Commande, selection ou donnees modifiees depuis PREVIEW; APPLY refuse.")
        if action == "cancel" and options.get("confirm_cancel") != "ANNULER":
            self.stdout.write(format_order_plan(plan))
            raise CommandError("Une annulation exige aussi --confirm-cancel ANNULER.")
        if plan.errors:
            self.stdout.write(format_order_plan(plan))
            raise CommandError("Apercu invalide; aucune commande modifiee.")
        try:
            result = apply_order_change_plan(plan)
        except OrderManagerError as exc:
            raise CommandError(f"{exc} Aucune modification validee.") from None
        except Exception as exc:
            raise CommandError(f"Echec ({type(exc).__name__}); refaites PREVIEW avant toute reprise.") from None
        self.stdout.write(f"{result['status']} | empreinte={result['fingerprint']} | commandes modifiees={result['changed']} | reprise={result['already_applied']}")
