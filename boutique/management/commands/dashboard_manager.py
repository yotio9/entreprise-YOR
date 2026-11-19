from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from boutique.services.dashboard_manager import (
    DashboardManagerError,
    collection_report,
    compare_periods,
    csv_export,
    format_dashboard,
    inventory_report,
    overview,
    period_bounds,
    report,
    revenue_summary,
    sales_analysis,
    top_collections,
    top_products,
)


class Command(BaseCommand):
    help = "Produit des statistiques en lecture seule a partir des commandes, ventes et stocks enregistres."

    def add_arguments(self, parser):
        parser.add_argument("--action", required=True, choices=("overview", "sales", "top-products", "top-collections", "revenue", "compare", "inventory", "report"))
        parser.add_argument("--period", choices=("day", "week", "month", "all"), help="Periode calendaire locale.")
        parser.add_argument("--date-from", help="Date incluse YYYY-MM-DD; a fournir avec date-to.")
        parser.add_argument("--date-to", help="Date incluse YYYY-MM-DD; a fournir avec date-from.")
        parser.add_argument("--compare-from", help="Debut de la deuxieme periode, pour compare uniquement.")
        parser.add_argument("--compare-to", help="Fin de la deuxieme periode, pour compare uniquement.")
        parser.add_argument("--granularity", choices=("day", "week", "month"), default="day")
        parser.add_argument("--sort", choices=("best", "least"), default="best")
        parser.add_argument("--report-type", choices=("daily", "weekly", "monthly", "product", "collection"))
        parser.add_argument("--product-id", type=int)
        parser.add_argument("--collection")
        parser.add_argument("--limit", type=int, default=10)
        parser.add_argument("--format", choices=("text", "json", "csv"), default="text")
        parser.add_argument("--output", help="Fichier de sortie a creer; un fichier existant n'est jamais ecrase.")
        parser.add_argument("--allow-remote-database", action="store_true", help="Autorise explicitement la lecture d'une base distante.")
        parser.add_argument("--allow-production", action="store_true", help="Autorise explicitement DEBUG=0.")

    def _guard_database(self, options):
        if not settings.DEBUG and not options["allow_production"]:
            raise CommandError("DEBUG=0 : consultation refusee sans --allow-production.")
        engine = settings.DATABASES["default"]["ENGINE"]
        host = (settings.DATABASES["default"].get("HOST") or "").strip().lower()
        if not (engine.endswith("sqlite3") or host in {"", "localhost", "127.0.0.1", "::1"}) and not options["allow_remote_database"]:
            raise CommandError("Base distante detectee; utilisez --allow-remote-database explicitement.")

    def _build(self, options):
        action = options["action"]
        period = options["period"]
        dates = {"date_from": options["date_from"], "date_to": options["date_to"]}
        if action == "overview":
            return overview(period or "month", **dates)
        if action == "sales":
            return sales_analysis(period or "month", **dates, granularity=options["granularity"])
        if action == "top-products":
            return top_products(options["limit"], options["sort"], period or "all", **dates)
        if action == "top-collections":
            return top_collections(options["limit"], period or "all", **dates)
        if action == "revenue":
            start, end, meta = period_bounds(period or "all", **dates)
            return {**meta, **revenue_summary(start, end)}
        if action == "compare":
            return compare_periods(period or "month", **dates, compare_from=options["compare_from"], compare_to=options["compare_to"])
        if action == "inventory":
            return inventory_report()
        if action == "report":
            if not options["report_type"]:
                raise DashboardManagerError("report exige --report-type.")
            if options["report_type"] == "product" and options["collection"]:
                raise DashboardManagerError("--collection ne s'utilise pas avec un rapport product.")
            if options["report_type"] == "collection" and options["product_id"]:
                raise DashboardManagerError("--product-id ne s'utilise pas avec un rapport collection.")
            return report(
                options["report_type"], period or "all", **dates,
                product_id=options["product_id"], collection=options["collection"], limit=options["limit"],
            )
        raise DashboardManagerError(f"Action inconnue: {action}.")

    def handle(self, *args, **options):
        self._guard_database(options)
        try:
            if options["limit"] < 1 or options["limit"] > 100:
                raise DashboardManagerError("--limit doit etre compris entre 1 et 100.")
            data = self._build(options)
            if options["format"] == "json":
                rendered = format_dashboard(data)
            elif options["format"] == "csv":
                rendered = csv_export(data)
            else:
                rendered = format_dashboard(data)
            if options["output"]:
                encoding = "utf-8" if options["format"] == "csv" else "utf-8"
                with Path(options["output"]).open("x", encoding=encoding, newline="") as output_file:
                    output_file.write(rendered)
                self.stdout.write(f"Rapport exporte: {options['output']}")
            else:
                self.stdout.write(rendered, ending="" if options["format"] == "csv" else "\n")
        except DashboardManagerError as exc:
            raise CommandError(str(exc)) from None
        except OSError as exc:
            raise CommandError(f"Ecriture du rapport impossible ({type(exc).__name__}).") from None
