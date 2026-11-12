from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone

from boutique.agents.jumia import validate_jumia_url
from boutique.models import JumiaOrderTask, Order


class Command(BaseCommand):
    help = "Relance une tache Jumia apres revue et correction humaine explicites."

    def add_arguments(self, parser):
        parser.add_argument("task_id", type=int)
        parser.add_argument("--human-reviewed", action="store_true", help="Confirme la revue du produit, du lien et du plafond.")

    def handle(self, *args, **options):
        if not options["human_reviewed"]:
            raise CommandError("La relance exige --human-reviewed apres verification manuelle.")
        with transaction.atomic():
            try:
                task = JumiaOrderTask.objects.select_for_update().select_related(
                    "order_item__order", "order_item__product"
                ).get(pk=options["task_id"])
            except JumiaOrderTask.DoesNotExist as exc:
                raise CommandError("Tache inconnue.") from exc
            if task.status not in (JumiaOrderTask.Status.NEEDS_HUMAN, JumiaOrderTask.Status.FAILED):
                raise CommandError("Seules les taches en revue humaine ou en echec peuvent etre relancees.")
            order = task.order_item.order
            product = task.order_item.product
            if (
                order.payment_status != Order.PaymentStatus.CONFIRMED
                or order.status not in (Order.Status.CONFIRMED, Order.Status.PREPARING)
            ):
                raise CommandError("La commande doit toujours etre payee et confirmee.")
            if not product or not product.source_price_limit or not validate_jumia_url(product.source_url):
                raise CommandError("Corrige d'abord le lien Jumia et le plafond fournisseur du produit.")
            task.source_url = product.source_url
            task.expected_product_name = product.name
            task.expected_color = product.color
            task.expected_size = task.order_item.size
            task.quantity = task.order_item.quantity
            task.price_limit = product.source_price_limit
            task.status = JumiaOrderTask.Status.PENDING
            task.max_attempts = max(
                task.max_attempts,
                task.attempt_count + settings.JUMIA_AGENT_MAX_ATTEMPTS,
            )
            task.available_at = timezone.now()
            task.claimed_at = None
            task.last_error_code = ""
            task.result_summary = "Relancee apres revue humaine ; l'agent reste en lecture seule."
            task.save()
        self.stdout.write(self.style.SUCCESS(f"Tache {task.pk} remise en file apres revue humaine."))
