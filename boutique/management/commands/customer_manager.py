from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from boutique.models import Order
from boutique.services.customer_manager import (
    CustomerManagerError,
    apply_customer_change_plan,
    build_customer_change_plan,
    customer_detail,
    customer_order_history,
    customer_statistics,
    export_customer_orders_csv,
    export_customers_csv,
    format_audit,
    format_customer_detail,
    format_customer_plan,
    format_customers,
    format_history,
    format_statistics,
    get_customer_change_history,
    search_customers,
    top_customers,
)


class Command(BaseCommand):
    help = "Consulte et gere les comptes clients avec apercu approuve et audit."

    def add_arguments(self, parser):
        parser.add_argument("--action", required=True, choices=("list", "detail", "history", "stats", "top", "export", "suspend", "reactivate", "audit"))
        parser.add_argument("--customer-id", type=int, action="append", dest="customer_ids", help="ID client; repetable pour les lots.")
        parser.add_argument("--email", action="append", dest="emails", help="Adresse client; repetable pour les lots.")
        parser.add_argument("--csv", help="CSV de selection clients avec colonne id/customer_id ou email.")
        parser.add_argument("--query", help="Recherche par identifiant, nom, e-mail, username ou telephone.")
        parser.add_argument("--status", choices=("all", "active", "suspended"), default="all", help="Filtre de statut des comptes.")
        parser.add_argument("--order-status", choices=[value for value, _ in Order.Status.choices], help="Filtre l'historique des commandes.")
        parser.add_argument("--date-from", help="Date incluse, format YYYY-MM-DD.")
        parser.add_argument("--date-to", help="Date incluse, format YYYY-MM-DD.")
        parser.add_argument("--reason", help="Raison obligatoire pour suspendre (5 a 500 caracteres).")
        parser.add_argument("--kind", choices=("customers", "orders"), default="customers", help="Type d'export CSV.")
        parser.add_argument("--limit", type=int, default=20, help="Nombre de clients pour top (1-100).")
        parser.add_argument("--fingerprint", help="Empreinte du journal d'audit.")
        parser.add_argument("--mode", choices=("preview", "apply"), default="preview")
        parser.add_argument("--confirm-digest", help="Empreinte exacte affichee par PREVIEW.")
        parser.add_argument("--confirm-suspension", help="Doit valoir SUSPENDRE pour suspendre un compte.")
        parser.add_argument("--output", help="Fichier CSV a creer (refuse d'ecraser un fichier existant).")
        parser.add_argument("--allow-remote-database", action="store_true", help="Autorise explicitement une base distante.")
        parser.add_argument("--allow-production", action="store_true", help="Autorise explicitement DEBUG=0.")

    def _guard_database(self, options):
        if not settings.DEBUG and not options["allow_production"]:
            raise CommandError("DEBUG=0 : operation refusee sans --allow-production.")
        engine = settings.DATABASES["default"]["ENGINE"]
        host = (settings.DATABASES["default"].get("HOST") or "").strip().lower()
        if not (engine.endswith("sqlite3") or host in {"", "localhost", "127.0.0.1", "::1"}) and not options["allow_remote_database"]:
            raise CommandError("Base distante detectee; utilisez --allow-remote-database explicitement.")

    def _selection(self, options):
        ids = options.get("customer_ids") or []
        emails = options.get("emails") or []
        if len(ids) + len(emails) > 1:
            raise CommandError("detail/history exigent un seul --customer-id ou --email.")
        if options.get("csv"):
            raise CommandError("detail/history n'acceptent pas de CSV; selectionnez un seul client.")
        return {"customer_id": ids[0] if ids else None, "email": emails[0] if emails else None}

    def handle(self, *args, **options):
        self._guard_database(options)
        action = options["action"]
        try:
            if action == "list":
                self.stdout.write(format_customers(search_customers({"query": options["query"], "status": options["status"], "date_from": options["date_from"], "date_to": options["date_to"]})))
                return
            if action == "detail":
                self.stdout.write(format_customer_detail(customer_detail(**self._selection(options))))
                return
            if action == "history":
                selection = self._selection(options)
                self.stdout.write(format_history(customer_order_history(**selection, order_status=options["order_status"], date_from=options["date_from"], date_to=options["date_to"])))
                return
            if action == "stats":
                self.stdout.write(format_statistics(customer_statistics(options["date_from"], options["date_to"])))
                return
            if action == "top":
                if not 1 <= options["limit"] <= 100:
                    raise CommandError("--limit doit etre compris entre 1 et 100.")
                self.stdout.write(format_customers(top_customers(options["limit"])))
                return
            if action == "export":
                if options["kind"] == "customers":
                    result = export_customers_csv({"query": options["query"], "status": options["status"], "date_from": options["date_from"], "date_to": options["date_to"]})
                else:
                    selection = self._selection(options)
                    result = export_customer_orders_csv(**selection, order_status=options["order_status"], date_from=options["date_from"], date_to=options["date_to"])
                if options["output"]:
                    with Path(options["output"]).open("x", encoding="utf-8", newline="") as output_file:
                        output_file.write(result)
                    self.stdout.write(f"CSV exporte: {options['output']}")
                else:
                    self.stdout.write(result, ending="")
                return
            if action == "audit":
                if options["mode"] != "preview" or not options["fingerprint"]:
                    raise CommandError("audit est en lecture seule et exige --fingerprint.")
                batch, rows = get_customer_change_history(options["fingerprint"])
                self.stdout.write(format_audit(batch, rows))
                return
            plan = build_customer_change_plan(action, {
                "customer_ids": options["customer_ids"],
                "emails": options["emails"],
                "csv": options["csv"],
                "reason": options["reason"],
            })
        except CustomerManagerError as exc:
            raise CommandError(str(exc)) from None
        except OSError as exc:
            raise CommandError(f"Export impossible ({type(exc).__name__}).") from None
        if options["mode"] == "preview":
            self.stdout.write(format_customer_plan(plan))
            return
        digest = (options.get("confirm_digest") or "").strip().lower()
        if not digest:
            self.stdout.write(format_customer_plan(plan))
            raise CommandError("APPLY exige --confirm-digest avec l'empreinte affichee.")
        if digest != plan.fingerprint:
            self.stdout.write(format_customer_plan(plan))
            raise CommandError("Compte, selection ou donnees modifies depuis PREVIEW; APPLY refuse.")
        if action == "suspend" and options.get("confirm_suspension") != "SUSPENDRE":
            self.stdout.write(format_customer_plan(plan))
            raise CommandError("La suspension exige aussi --confirm-suspension SUSPENDRE.")
        if plan.errors:
            self.stdout.write(format_customer_plan(plan))
            raise CommandError("Apercu invalide; aucun compte modifie.")
        try:
            result = apply_customer_change_plan(plan)
        except CustomerManagerError as exc:
            raise CommandError(f"{exc} Aucun compte modifie.") from None
        except Exception as exc:
            raise CommandError(f"Echec ({type(exc).__name__}); refaites PREVIEW avant toute reprise.") from None
        self.stdout.write(f"{result['status']} | empreinte={result['fingerprint']} | comptes modifies={result['changed']} | reprise={result['already_applied']}")
