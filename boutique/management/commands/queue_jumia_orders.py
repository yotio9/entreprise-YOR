from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db.models import Prefetch

from boutique.agents.jumia import validate_jumia_url
from boutique.models import JumiaOrderTask, Order, OrderItem


class Command(BaseCommand):
    help = "Place des articles fournisseur eligibles dans la file Jumia en mode simulation."

    def add_arguments(self, parser):
        parser.add_argument("--order-ref", help="Ne traiter qu'une reference de commande.")
        parser.add_argument("--dry-run", action="store_true", help="N'ecrit aucune tache.")

    def handle(self, *args, **options):
        if settings.JUMIA_AGENT_MODE != "simulation":
            raise CommandError("Le traitement Jumia est limite au mode simulation.")
        orders = Order.objects.filter(
            payment_status=Order.PaymentStatus.CONFIRMED,
            status__in=(Order.Status.CONFIRMED, Order.Status.PREPARING),
        ).prefetch_related(
            Prefetch("items", queryset=OrderItem.objects.select_related("product"))
        )
        if options["order_ref"]:
            if not Order.objects.filter(reference=options["order_ref"]).exists():
                raise CommandError("Commande inconnue.")
            orders = orders.filter(reference=options["order_ref"])

        created = skipped = 0
        for order in orders:
            for item in order.items.all():
                product = item.product
                if not product or not product.source_url or not validate_jumia_url(product.source_url):
                    skipped += 1
                    continue
                if JumiaOrderTask.objects.filter(order_item=item).exists():
                    skipped += 1
                    continue
                if options["dry_run"]:
                    created += 1
                    continue
                _, was_created = JumiaOrderTask.objects.get_or_create(
                    order_item=item,
                    defaults={
                        "source_url": product.source_url,
                        "expected_product_name": product.name,
                        "expected_color": product.color,
                        "expected_size": item.size,
                        "quantity": item.quantity,
                        "price_limit": product.source_price_limit,
                        "max_attempts": settings.JUMIA_AGENT_MAX_ATTEMPTS,
                    },
                )
                if was_created:
                    created += 1
                else:
                    skipped += 1
        action = "a creer" if options["dry_run"] else "creees"
        self.stdout.write(self.style.SUCCESS(f"{created} tache(s) {action} ; {skipped} ignoree(s)."))
        self.stdout.write("Aucun panier, achat ou paiement n'a ete effectue.")
