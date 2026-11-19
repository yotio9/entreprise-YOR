"""Read-only order queries and preview-first, audited order transitions."""

import csv
import hashlib
import io
import json
from dataclasses import dataclass, field
from datetime import date, datetime, time
from decimal import Decimal
from pathlib import Path

from django.db import IntegrityError, transaction
from django.db.models import Prefetch
from django.db.models import DecimalField, F, Q, Sum, Value
from django.db.models.functions import Greatest
from django.utils import timezone

from boutique.models import Order, OrderChangeBatch, OrderChangeLog, OrderItem, User

MAX_SELECTION_ROWS = 5000


class OrderManagerError(Exception):
    pass


class StaleOrderPlan(OrderManagerError):
    pass


@dataclass
class OrderPlanRow:
    order_id: int
    reference: str
    version: dict
    before: dict
    after: dict
    errors: list = field(default_factory=list)


@dataclass
class OrderChangePlan:
    operation: str
    params: dict
    rows: list
    errors: list
    fingerprint: str

    @property
    def has_changes(self):
        return any(row.before != row.after for row in self.rows)


def _json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)


def _load_orders(queryset):
    return list(queryset.select_related("user").prefetch_related("items"))


def _item_snapshot(order):
    return [
        {
            "id": item.pk,
            "product_name": item.product_name,
            "size": item.size,
            "personalization": item.personalization,
            "quantity": item.quantity,
            "unit_price": format(item.unit_price, ".2f"),
        }
        for item in order.items.all().order_by("pk")
    ]


def _order_version(order):
    return {
        "reference": order.reference,
        "status": order.status,
        "payment_status": order.payment_status,
        "payment_method": order.payment_method,
        "customer_name": order.customer_name,
        "customer_email": order.customer_email,
        "phone": order.phone,
        "address": order.address,
        "city": order.city,
        "total": format(order.total, ".2f"),
        "shipping_carrier": order.shipping_carrier,
        "tracking_number": order.tracking_number,
        "delivered_at": order.delivered_at.isoformat() if order.delivered_at else None,
        "loyalty_points_awarded": order.loyalty_points_awarded,
        "customer_loyalty_points": order.user.loyalty_points,
        "created_at": order.created_at.isoformat(),
        "items": _item_snapshot(order),
    }


def _parse_datetime(raw, end=False):
    if not raw:
        return None
    try:
        parsed_date = date.fromisoformat(raw)
    except ValueError:
        raise OrderManagerError("Dates attendues au format YYYY-MM-DD.") from None
    value = datetime.combine(parsed_date, time.max if end else time.min)
    if timezone.is_naive(value):
        value = timezone.make_aware(value, timezone.get_current_timezone())
    return value


def _filtered_orders(options):
    orders = Order.objects.all()
    query = (options.get("query") or "").strip()
    if query:
        orders = orders.filter(
            Q(reference__icontains=query)
            | Q(customer_name__icontains=query)
            | Q(customer_email__icontains=query)
            | Q(phone__icontains=query)
        )
    if options.get("status"):
        orders = orders.filter(status=options["status"])
    if options.get("payment_status"):
        orders = orders.filter(payment_status=options["payment_status"])
    date_from = _parse_datetime(options.get("date_from"))
    date_to = _parse_datetime(options.get("date_to"), end=True)
    if date_from and date_to and date_from > date_to:
        raise OrderManagerError("date-from doit etre inferieure ou egale a date-to.")
    if date_from:
        orders = orders.filter(created_at__gte=date_from)
    if date_to:
        orders = orders.filter(created_at__lte=date_to)
    return orders.order_by("-created_at", "-pk")


def search_orders(options):
    orders = _filtered_orders(options)
    results = []
    for order in _load_orders(orders[:500]):
        results.append({
            "reference": order.reference,
            "created_at": timezone.localtime(order.created_at).isoformat(timespec="minutes"),
            "customer": order.customer_name,
            "city": order.city,
            "status": order.status,
            "status_display": order.get_status_display(),
            "payment_status": order.payment_status,
            "payment_status_display": order.get_payment_status_display(),
            "total": format(order.total, ".2f"),
        })
    return results


def order_detail(reference):
    order = Order.objects.select_related("user").prefetch_related("items").filter(reference__iexact=reference.strip()).first()
    if not order:
        raise OrderManagerError(f"Commande introuvable: {reference}.")
    return {
        "reference": order.reference,
        "created_at": timezone.localtime(order.created_at).isoformat(timespec="minutes"),
        "status": order.status,
        "status_display": order.get_status_display(),
        "payment_status": order.payment_status,
        "payment_status_display": order.get_payment_status_display(),
        "payment_method": order.get_payment_method_display(),
        "customer": {
            "name": order.customer_name,
            "email": order.customer_email,
            "phone": order.phone,
            "address": order.address,
            "city": order.city,
            "delivery_note": order.delivery_note,
        },
        "amounts": {
            "subtotal": format(order.subtotal, ".2f"),
            "shipping_fee": format(order.shipping_fee, ".2f"),
            "discount_amount": format(order.discount_amount, ".2f"),
            "total": format(order.total, ".2f"),
        },
        "shipping": {
            "carrier": order.shipping_carrier,
            "tracking_number": order.tracking_number,
            "delivered_at": order.delivered_at.isoformat() if order.delivered_at else None,
        },
        "items": [
            {
                "name": item.product_name,
                "quantity": item.quantity,
                "size": item.size,
                "personalization": item.personalization,
                "unit_price": format(item.unit_price, ".2f"),
                "line_total": format(item.line_total, ".2f"),
            }
            for item in order.items.all().order_by("pk")
        ],
    }


def order_statistics(options):
    orders = _filtered_orders(options)
    all_count = orders.count()
    cancelled = orders.filter(status=Order.Status.CANCELLED)
    pending = orders.filter(status=Order.Status.AWAITING_PAYMENT)
    active_paid = orders.exclude(status=Order.Status.CANCELLED).filter(payment_status=Order.PaymentStatus.CONFIRMED)
    net_total = Greatest(
        F("subtotal") + F("shipping_fee") - F("discount_amount"),
        Value(Decimal("0.00")),
        output_field=DecimalField(max_digits=12, decimal_places=2),
    )
    return {
        "orders": all_count,
        "revenue_confirmed_not_cancelled": format(active_paid.aggregate(value=Sum(net_total))["value"] or Decimal("0.00"), ".2f"),
        "pending": pending.count(),
        "cancelled": cancelled.count(),
        "shipped": orders.filter(status=Order.Status.SHIPPED).count(),
        "delivered": orders.filter(status=Order.Status.DELIVERED).count(),
        "revenue_definition": "Somme des totaux nets des commandes payees confirmees et non annulees.",
    }


def _csv_safe(value):
    text = str(value or "")
    return "'" + text if text[:1] in ("=", "+", "-", "@", "\t", "\r") else text


def export_orders_csv(options):
    orders = _load_orders(_filtered_orders(options))
    output = io.StringIO(newline="")
    writer = csv.writer(output)
    writer.writerow(("reference", "created_at", "customer_name", "city", "status", "payment_status", "payment_method", "subtotal", "shipping_fee", "discount_amount", "total", "shipping_carrier", "tracking_number", "items"))
    for order in orders:
        items = "; ".join(f"{item.quantity}x {item.product_name} [{item.unit_price:.2f}]" for item in order.items.all().order_by("pk"))
        writer.writerow(tuple(_csv_safe(value) for value in (
            order.reference, timezone.localtime(order.created_at).isoformat(timespec="minutes"), order.customer_name,
            order.city, order.status, order.payment_status, order.payment_method, order.subtotal, order.shipping_fee,
            order.discount_amount, order.total, order.shipping_carrier, order.tracking_number, items,
        )))
    return "\ufeff" + output.getvalue()


def _selection(options):
    references = [str(value).strip() for value in (options.get("references") or []) if str(value).strip()]
    source_meta = {}
    csv_path = options.get("csv")
    if csv_path:
        if references:
            raise OrderManagerError("Utilisez --csv ou --reference, pas les deux.")
        path = Path(csv_path)
        try:
            raw = path.read_bytes()
            text = raw.decode("utf-8-sig")
        except (OSError, UnicodeDecodeError) as exc:
            raise OrderManagerError(f"CSV illisible ({type(exc).__name__}).") from None
        if len(raw) > 25 * 1024 * 1024:
            raise OrderManagerError("CSV trop volumineux (maximum 25 Mo).")
        try:
            dialect = csv.Sniffer().sniff(text[:4096], delimiters=",;\t")
        except csv.Error:
            dialect = csv.excel
        reader = csv.DictReader(io.StringIO(text, newline=""), dialect=dialect)
        if not reader.fieldnames:
            raise OrderManagerError("CSV sans en-tete.")
        ref_header = next((header for header in reader.fieldnames if header and header.strip().lower() in ("reference", "order_reference")), None)
        if not ref_header:
            raise OrderManagerError("Le CSV doit contenir une colonne reference.")
        for row in reader:
            references.append((row.get(ref_header) or "").strip())
            if len(references) > MAX_SELECTION_ROWS:
                raise OrderManagerError("Maximum 5 000 commandes par lot.")
        source_meta = {"csv_sha256": hashlib.sha256(raw).hexdigest(), "source_filename": path.name}
    if not references:
        raise OrderManagerError("Fournissez au moins un --reference ou un --csv.")
    if len(references) > MAX_SELECTION_ROWS:
        raise OrderManagerError("Maximum 5 000 commandes par lot.")
    if any(not ref for ref in references):
        raise OrderManagerError("Le CSV contient une reference vide.")
    normalized = [ref.upper() for ref in references]
    if len(normalized) != len(set(normalized)):
        raise OrderManagerError("Le lot contient des references dupliquees.")
    orders = _load_orders(Order.objects.filter(reference__in=normalized))
    found = {order.reference.upper(): order for order in orders}
    missing = [ref for ref in normalized if ref not in found]
    if missing:
        raise OrderManagerError("Commandes introuvables: " + ", ".join(missing))
    return [found[ref] for ref in normalized], source_meta


def _transition_error(order, target):
    status = order.status
    if status == target:
        return None
    if status in (Order.Status.CANCELLED, Order.Status.DELIVERED):
        return "Une commande annulee ou livree est terminale."
    if target == Order.Status.CANCELLED:
        if status in (Order.Status.AWAITING_PAYMENT, Order.Status.CONFIRMED, Order.Status.PREPARING):
            return None
        return "Une commande expediee ne peut pas etre annulee avec cette commande."
    if target not in (Order.Status.CONFIRMED, Order.Status.PREPARING, Order.Status.SHIPPED, Order.Status.DELIVERED):
        return "Transition vers ce statut interdite."
    allowed_next = {
        Order.Status.AWAITING_PAYMENT: {Order.Status.CONFIRMED, Order.Status.PREPARING},
        Order.Status.CONFIRMED: {Order.Status.PREPARING},
        Order.Status.PREPARING: {Order.Status.SHIPPED},
        Order.Status.SHIPPED: {Order.Status.DELIVERED},
    }
    if target not in allowed_next.get(status, set()):
        return f"Transition {status} -> {target} interdite."
    cod_due = order.payment_method == Order.PaymentMethod.CASH_ON_DELIVERY and order.payment_status == Order.PaymentStatus.DUE_ON_DELIVERY
    paid = order.payment_status == Order.PaymentStatus.CONFIRMED
    if target == Order.Status.CONFIRMED and not paid:
        return "Le statut commande CONFIRMED exige un paiement deja confirme."
    if target in (Order.Status.PREPARING, Order.Status.SHIPPED) and not (paid or cod_due):
        return "Paiement non confirme; seule une commande COD a regler a la livraison peut avancer."
    if target == Order.Status.DELIVERED and not paid:
        return "La livraison exige un paiement deja confirme."
    return None


def _make_row(order, action, options):
    version = _order_version(order)
    before = {
        "status": order.status,
        "payment_status": order.payment_status,
        "shipping_carrier": order.shipping_carrier,
        "tracking_number": order.tracking_number,
        "delivered_at": order.delivered_at.isoformat() if order.delivered_at else None,
        "loyalty_points_awarded": order.loyalty_points_awarded,
    }
    errors = []
    if action == "cancel":
        reason = (options.get("reason") or "").strip()
        if not reason or len(reason) < 5 or len(reason) > 500:
            errors.append("La raison d'annulation est obligatoire (5 a 500 caracteres).")
        if order.status == Order.Status.CANCELLED:
            errors.append("La commande est deja annulee.")
        error = _transition_error(order, Order.Status.CANCELLED)
        if error:
            errors.append(error)
        after = {**before, "status": Order.Status.CANCELLED, "cancellation_reason": reason}
    else:
        target = options.get("target_status")
        error = _transition_error(order, target)
        if error:
            errors.append(error)
        carrier = (options.get("carrier") or "").strip()
        tracking = (options.get("tracking_number") or "").strip()
        if target == Order.Status.SHIPPED and carrier and len(carrier) > 100:
            errors.append("Transporteur: maximum 100 caracteres.")
        if tracking and len(tracking) > 100:
            errors.append("Numero de suivi: maximum 100 caracteres.")
        if target != Order.Status.SHIPPED and (carrier or tracking):
            errors.append("Transporteur/numero de suivi ne sont modifiables qu'au passage a SHIPPED.")
        after = {**before, "status": target}
        if target == Order.Status.SHIPPED:
            after["shipping_carrier"] = carrier or order.shipping_carrier
            after["tracking_number"] = tracking or order.tracking_number
        if target == Order.Status.DELIVERED and not order.delivered_at:
            after["delivered_at"] = "SET_AT_APPLY"
            if not order.loyalty_points_awarded and order.total >= Decimal("1000.00"):
                before["customer_loyalty_points"] = order.user.loyalty_points
                after["loyalty_points_awarded"] = int(order.total // Decimal("1000"))
                after["customer_loyalty_points"] = order.user.loyalty_points + after["loyalty_points_awarded"]
    return OrderPlanRow(order.pk, order.reference, version, before, after, errors)


def _fingerprint(operation, params, rows, source_meta):
    payload = {
        "version": 1,
        "operation": operation,
        "params": params,
        "source": source_meta,
        "rows": [{"id": row.order_id, "reference": row.reference, "version": row.version, "before": row.before, "after": row.after, "errors": row.errors} for row in rows],
    }
    return hashlib.sha256(_json(payload).encode("utf-8")).hexdigest()


def build_order_change_plan(action, options):
    if action not in ("transition", "cancel"):
        raise OrderManagerError(f"Action de modification inconnue: {action}.")
    if action == "transition" and options.get("target_status") == Order.Status.CANCELLED:
        raise OrderManagerError("Utilisez l'action cancel avec une raison obligatoire.")
    if action == "cancel" and (options.get("carrier") or options.get("tracking_number")):
        raise OrderManagerError("Les donnees d'expedition ne peuvent pas etre combinees a une annulation.")
    orders, source_meta = _selection(options)
    rows = [_make_row(order, action, options) for order in orders]
    params = {
        "target_status": options.get("target_status") if action == "transition" else Order.Status.CANCELLED,
        "reason": (options.get("reason") or "").strip() if action == "cancel" else "",
        "carrier": (options.get("carrier") or "").strip(),
        "tracking_number": (options.get("tracking_number") or "").strip(),
    }
    errors = [f"{row.reference}: {message}" for row in rows for message in row.errors]
    fingerprint = _fingerprint(action, params, rows, source_meta)
    return OrderChangePlan(action, params, rows, errors, fingerprint)


def format_order_plan(plan):
    lines = [f"PREVIEW {plan.operation}", f"Empreinte a approuver: {plan.fingerprint}", f"Commandes concernees: {len(plan.rows)}"]
    for row in plan.rows:
        lines.append(f"{row.reference} | {row.version['customer_name']} | {row.version['status']} / paiement={row.version['payment_status']} | total={row.version['total']}")
        lines.append(f"  avant={_json(row.before)}")
        lines.append(f"  apres={_json(row.after)}")
        for item in row.version["items"]:
            lines.append(f"  article: {item['quantity']} x {item['product_name']} @ {item['unit_price']}")
        lines.extend(f"  ERREUR: {message}" for message in row.errors)
    lines.extend(f"CONFLIT: {message}" for message in plan.errors)
    lines.append("Aucune ecriture effectuee en PREVIEW.")
    if plan.operation == "cancel":
        lines.append("Annulation: APPLY exige --confirm-cancel ANNULER en plus de l'empreinte.")
    return "\n".join(lines)


def apply_order_change_plan(plan):
    if plan.errors:
        raise OrderManagerError("L'apercu contient des erreurs; aucune commande modifiee.")
    if not plan.rows:
        raise OrderManagerError("Aucune commande a modifier.")
    if not plan.has_changes:
        return {"status": "no_changes", "fingerprint": plan.fingerprint, "changed": 0, "already_applied": False}
    try:
        with transaction.atomic():
            existing = OrderChangeBatch.objects.select_for_update().filter(fingerprint=plan.fingerprint).first()
            if existing:
                return {"status": existing.status, "fingerprint": existing.fingerprint, "changed": existing.changes.count(), "already_applied": True}
            orders = {
                order.pk: order
                for order in Order.objects.select_for_update().select_related("user").prefetch_related(
                    Prefetch("items", queryset=OrderItem.objects.select_for_update().order_by("pk"))
                ).filter(pk__in=[row.order_id for row in plan.rows]).order_by("pk")
            }
            for row in plan.rows:
                order = orders.get(row.order_id)
                if not order:
                    raise StaleOrderPlan(f"Commande {row.reference} introuvable depuis PREVIEW.")
                if _order_version(order) != row.version:
                    raise StaleOrderPlan(f"Commande {row.reference} modifiee depuis PREVIEW.")
                error = _transition_error(order, row.after["status"])
                if error:
                    raise StaleOrderPlan(f"Commande {row.reference}: {error}")

            delivery_user_ids = sorted({orders[row.order_id].user_id for row in plan.rows if row.after["status"] == Order.Status.DELIVERED})
            users = {user.pk: user for user in User.objects.select_for_update().filter(pk__in=delivery_user_ids).order_by("pk")}
            for row in plan.rows:
                if "customer_loyalty_points" in row.after:
                    customer = users[orders[row.order_id].user_id]
                    if customer.loyalty_points != row.version["customer_loyalty_points"]:
                        raise StaleOrderPlan(f"Le solde fidelite du client pour {row.reference} a change depuis PREVIEW.")

            batch = OrderChangeBatch.objects.create(
                fingerprint=plan.fingerprint,
                operation=plan.operation,
                details={"params": plan.params, "orders": len(plan.rows)},
            )
            for row in plan.rows:
                order = orders[row.order_id]
                after = dict(row.after)
                update_fields = ["status"]
                order.status = after["status"]
                if after["status"] == Order.Status.SHIPPED:
                    order.shipping_carrier = after["shipping_carrier"]
                    order.tracking_number = after["tracking_number"]
                    update_fields.extend(("shipping_carrier", "tracking_number"))
                if after["status"] == Order.Status.DELIVERED:
                    if not order.delivered_at:
                        order.delivered_at = timezone.now()
                        after["delivered_at"] = order.delivered_at.isoformat()
                        update_fields.append("delivered_at")
                    if "customer_loyalty_points" in after:
                        new_balance = after["customer_loyalty_points"]
                        order.loyalty_points_awarded = after["loyalty_points_awarded"]
                        customer = users[order.user_id]
                        customer.loyalty_points += new_balance - row.version["customer_loyalty_points"]
                        customer.save(update_fields=("loyalty_points",))
                        update_fields.append("loyalty_points_awarded")
                order.save(update_fields=tuple(update_fields))
                OrderChangeLog.objects.create(
                    batch=batch,
                    order=order,
                    reference_snapshot=order.reference,
                    before=row.before,
                    after=after,
                )
        return {"status": "applied", "fingerprint": plan.fingerprint, "changed": len(plan.rows), "already_applied": False}
    except IntegrityError:
        existing = OrderChangeBatch.objects.filter(fingerprint=plan.fingerprint).first()
        if existing:
            return {"status": existing.status, "fingerprint": existing.fingerprint, "changed": existing.changes.count(), "already_applied": True}
        raise


def format_search(results):
    lines = [f"Commandes: {len(results)}" + (" (maximum 500 affichees)." if len(results) == 500 else ".")]
    for row in results:
        lines.append(f"{row['reference']} | {row['created_at']} | {row['customer']} ({row['city']}) | {row['status_display']} | paiement={row['payment_status_display']} | total={row['total']}")
    return "\n".join(lines)


def format_detail(detail):
    lines = [f"Commande {detail['reference']} | {detail['created_at']}", f"Statut: {detail['status_display']} | paiement: {detail['payment_status_display']} ({detail['payment_method']})"]
    customer = detail["customer"]
    lines.append(f"Client: {customer['name']} | {customer['email']} | {customer['phone']} | {customer['address']}, {customer['city']}")
    for item in detail["items"]:
        lines.append(f"{item['quantity']} x {item['name']} | taille={item['size'] or '-'} | {item['unit_price']} = {item['line_total']}")
    lines.append("Montants: " + _json(detail["amounts"]))
    lines.append("Expedition: " + _json(detail["shipping"]))
    return "\n".join(lines)


def format_statistics(stats):
    return "\n".join((
        f"Commandes: {stats['orders']}",
        f"Chiffre d'affaires confirme (hors annulees): {stats['revenue_confirmed_not_cancelled']} FCFA",
        f"En attente: {stats['pending']}",
        f"Annulees: {stats['cancelled']}",
        f"Expediees: {stats['shipped']}",
        f"Livrees: {stats['delivered']}",
        stats["revenue_definition"],
    ))


def get_order_change_history(fingerprint):
    batch = OrderChangeBatch.objects.filter(fingerprint=fingerprint.strip().lower()).first()
    if not batch:
        raise OrderManagerError("Journal introuvable pour cette empreinte.")
    return batch, list(batch.changes.values("reference_snapshot", "before", "after"))


def format_history(batch, rows):
    lines = [f"{batch.operation} | {batch.status} | {batch.created_at.isoformat()} | {batch.fingerprint}"]
    lines.extend(f"{row['reference_snapshot']} | avant={_json(row['before'])} | apres={_json(row['after'])}" for row in rows)
    return "\n".join(lines)
