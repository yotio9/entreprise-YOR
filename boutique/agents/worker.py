import logging
import socket
import time
import uuid
from datetime import timedelta

from django.conf import settings
from django.core.exceptions import ImproperlyConfigured
from django.db import connection, transaction
from django.db.models import Q
from django.utils import timezone

from boutique.models import JumiaOrderAttempt, JumiaOrderTask
from .jumia import HumanReviewRequired, RetryableInspectionError, assess_listing, inspect_jumia_product
from .llm import analyze_supplier_listing

logger = logging.getLogger(__name__)
LEASE = timedelta(minutes=10)


def recover_stale_tasks(now=None):
    now = now or timezone.now()
    stale_before = now - LEASE
    with transaction.atomic():
        stale = list(
            JumiaOrderTask.objects.select_for_update(skip_locked=True)
            .filter(status=JumiaOrderTask.Status.RUNNING, claimed_at__lt=stale_before)
        )
        for task in stale:
            attempt = task.attempts.filter(number=task.attempt_count).first()
            if attempt and attempt.status == JumiaOrderAttempt.Status.STARTED:
                attempt.status = JumiaOrderAttempt.Status.INTERRUPTED
                attempt.summary = "Worker interrompu avant la fin de inspection."
                attempt.finished_at = now
                attempt.save(update_fields=("status", "summary", "finished_at"))
            task.claimed_at = None
            task.last_error_code = "worker_interrupted"
            task.result_summary = "Tentative interrompue ; reprise automatique sans action achat."
            task.status = (
                JumiaOrderTask.Status.PENDING
                if task.attempt_count < task.max_attempts
                else JumiaOrderTask.Status.FAILED
            )
            task.available_at = now
            task.save(update_fields=(
                "claimed_at", "last_error_code", "result_summary", "status",
                "available_at", "updated_at",
            ))


def claim_next_task(worker_id=None):
    if connection.vendor != "postgresql" or not connection.features.has_select_for_update_skip_locked:
        raise ImproperlyConfigured(
            "Le worker Jumia exige PostgreSQL pour verrouiller les taches concurrentes."
        )
    now = timezone.now()
    recover_stale_tasks(now)
    with transaction.atomic():
        task = (
            JumiaOrderTask.objects.select_for_update(skip_locked=True)
            .filter(status=JumiaOrderTask.Status.PENDING, available_at__lte=now)
            .order_by("available_at", "created_at")
            .first()
        )
        if task is None:
            return None, None
        task.status = JumiaOrderTask.Status.RUNNING
        task.claimed_at = now
        task.attempt_count += 1
        task.save(update_fields=("status", "claimed_at", "attempt_count", "updated_at"))
        attempt = JumiaOrderAttempt.objects.create(
            task=task,
            number=task.attempt_count,
            status=JumiaOrderAttempt.Status.STARTED,
            summary=f"Inspection en lecture seule par {worker_id or 'worker'}.",
        )
        return task, attempt


def process_task(task, attempt, inspector=inspect_jumia_product, llm_analyzer=analyze_supplier_listing):
    now = timezone.now()
    try:
        listing = inspector(task)
        assessment = assess_listing(task, listing)
        advisory = llm_analyzer(task, listing)
        result_data = {
            **listing,
            "assessment": assessment,
            "llm_advisory": advisory,
            "actions_taken": ["opened_supplier_listing", "read_visible_product_data"],
            "actions_not_taken": ["login", "variant_selection", "add_to_cart", "checkout", "payment"],
        }
        if assessment["prepared"]:
            status = JumiaOrderTask.Status.PREPARED
            attempt_status = JumiaOrderAttempt.Status.PREPARED
            summary = (
                "Produit, variante, disponibilite et plafond de prix verifies. "
                "Aucun panier ni achat n'a ete effectue ; validation humaine obligatoire."
            )
        else:
            status = JumiaOrderTask.Status.NEEDS_HUMAN
            attempt_status = JumiaOrderAttempt.Status.NEEDS_HUMAN
            summary = "Verification humaine requise : " + ", ".join(assessment["issues"])
        with transaction.atomic():
            locked = JumiaOrderTask.objects.select_for_update().get(pk=task.pk)
            locked.status = status
            locked.claimed_at = None
            locked.verified_title = listing.get("title", "")[:300]
            locked.verified_price = listing.get("price")
            locked.verified_currency = listing.get("currency", "")[:8]
            locked.availability = listing.get("availability", "")[:24]
            locked.result_summary = summary
            locked.result_data = result_data
            locked.last_error_code = ""
            locked.save(update_fields=(
                "status", "claimed_at", "verified_title", "verified_price",
                "verified_currency", "availability", "result_summary",
                "result_data", "last_error_code", "updated_at",
            ))
            attempt.status = attempt_status
            attempt.summary = summary
            attempt.result_data = result_data
            attempt.finished_at = now
            attempt.save(update_fields=("status", "summary", "result_data", "finished_at"))
        return status
    except HumanReviewRequired as exc:
        summary = str(exc)[:500]
        _finish_human_review(task, attempt, summary, "supplier_verification_required")
        return JumiaOrderTask.Status.NEEDS_HUMAN
    except RetryableInspectionError as exc:
        summary = str(exc)[:500]
        return _finish_retryable(task, attempt, summary, "supplier_unavailable")
    except Exception as exc:
        # Deliberately omit exception messages and tracebacks: they may contain URLs or secrets.
        logger.warning("Jumia task %s failed (%s)", task.pk, type(exc).__name__)
        return _finish_retryable(task, attempt, "Inspection temporairement indisponible.", "inspection_error")


def _finish_human_review(task, attempt, summary, error_code):
    now = timezone.now()
    with transaction.atomic():
        locked = JumiaOrderTask.objects.select_for_update().get(pk=task.pk)
        locked.status = JumiaOrderTask.Status.NEEDS_HUMAN
        locked.claimed_at = None
        locked.result_summary = summary
        locked.last_error_code = error_code
        locked.save(update_fields=("status", "claimed_at", "result_summary", "last_error_code", "updated_at"))
        attempt.status = JumiaOrderAttempt.Status.NEEDS_HUMAN
        attempt.summary = summary
        attempt.finished_at = now
        attempt.save(update_fields=("status", "summary", "finished_at"))


def _finish_retryable(task, attempt, summary, error_code):
    now = timezone.now()
    with transaction.atomic():
        locked = JumiaOrderTask.objects.select_for_update().get(pk=task.pk)
        retry = locked.attempt_count < locked.max_attempts
        locked.status = JumiaOrderTask.Status.PENDING if retry else JumiaOrderTask.Status.FAILED
        locked.claimed_at = None
        locked.available_at = now + timedelta(seconds=min(300, 15 * (2 ** max(0, locked.attempt_count - 1)))) if retry else now
        locked.result_summary = summary
        locked.last_error_code = error_code
        locked.save(update_fields=(
            "status", "claimed_at", "available_at", "result_summary",
            "last_error_code", "updated_at",
        ))
        attempt.status = JumiaOrderAttempt.Status.RETRYABLE_FAILURE if retry else JumiaOrderAttempt.Status.PERMANENT_FAILURE
        attempt.summary = summary
        attempt.finished_at = now
        attempt.save(update_fields=("status", "summary", "finished_at"))
    return locked.status


def run_worker(once=False, limit=None, poll_seconds=None):
    if not settings.JUMIA_AGENT_ENABLED:
        raise ImproperlyConfigured("JUMIA_AGENT_ENABLED=1 est necessaire pour demarrer le worker.")
    if settings.JUMIA_AGENT_MODE != "simulation":
        raise ImproperlyConfigured("Le worker Jumia ne fonctionne qu'en mode simulation.")
    worker_id = f"{socket.gethostname()}-{uuid.uuid4().hex[:8]}"
    processed = 0
    while limit is None or processed < limit:
        task, attempt = claim_next_task(worker_id)
        if task is None:
            if once:
                return processed
            time.sleep(poll_seconds or settings.JUMIA_AGENT_POLL_SECONDS)
            continue
        status = process_task(task, attempt)
        processed += 1
        logger.info("Jumia task %s completed with status %s", task.pk, status)
        if once:
            return processed
    return processed
