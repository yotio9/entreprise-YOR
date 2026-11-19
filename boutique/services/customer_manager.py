"""Customer queries, purchase summaries, and audited account suspension."""

import csv
import hashlib
import io
import json
from dataclasses import dataclass, field
from datetime import date, datetime, time
from decimal import Decimal
from pathlib import Path

from django.db import IntegrityError, transaction
from django.db.models import Count, DecimalField, F, Max, Q, Sum, Value
from django.db.models.functions import Greatest
from django.utils import timezone

from boutique.models import CustomerChangeBatch, CustomerChangeLog, Order, User

MAX_CUSTOMER_BATCH = 1000
MAX_CUSTOMER_CSV_BYTES = 10 * 1024 * 1024


class CustomerManagerError(Exception):
    pass


class StaleCustomerPlan(CustomerManagerError):
    pass


@dataclass
class CustomerPlanRow:
    customer_id: int
    label: str
    version: dict
    before: dict
    after: dict
    errors: list = field(default_factory=list)


@dataclass
class CustomerChangePlan:
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


def _date_bounds(date_from=None, date_to=None):
    def parse(raw, end=False):
        if not raw:
            return None
        try:
            day = date.fromisoformat(raw)
        except ValueError:
            raise CustomerManagerError("Dates attendues au format YYYY-MM-DD.") from None
        value = datetime.combine(day, time.max if end else time.min)
        return timezone.make_aware(value, timezone.get_current_timezone())
    start, end = parse(date_from), parse(date_to, end=True)
    if start and end and start > end:
        raise CustomerManagerError("date-from doit etre inferieure ou egale a date-to.")
    return start, end


def _base_customers(options=None):
    options = options or {}
    customers = User.objects.filter(is_staff=False, is_superuser=False)
    status = options.get("status") or "all"
    if status == "active":
        customers = customers.filter(is_active=True)
    elif status == "suspended":
        customers = customers.filter(is_active=False)
    query = (options.get("query") or "").strip()
    if query:
        predicates = Q(username__icontains=query) | Q(email__icontains=query) | Q(first_name__icontains=query) | Q(last_name__icontains=query) | Q(phone__icontains=query)
        if query.isdigit():
            predicates |= Q(pk=int(query))
        customers = customers.filter(predicates)
    start, end = _date_bounds(options.get("date_from"), options.get("date_to"))
    if start:
        customers = customers.filter(date_joined__gte=start)
    if end:
        customers = customers.filter(date_joined__lte=end)
    return customers


def _purchase_condition():
    return Q(orders__payment_status=Order.PaymentStatus.CONFIRMED) & ~Q(orders__status=Order.Status.CANCELLED)


def _purchase_total_expression():
    return Greatest(
        F("orders__subtotal") + F("orders__shipping_fee") - F("orders__discount_amount"),
        Value(Decimal("0.00")),
        output_field=DecimalField(max_digits=12, decimal_places=2),
    )


def _customer_metrics(queryset):
    return queryset.annotate(
        order_count=Count("orders", distinct=True),
        purchase_count=Count("orders", filter=_purchase_condition(), distinct=True),
        total_spent=Sum(_purchase_total_expression(), filter=_purchase_condition(), default=Decimal("0.00")),
        last_purchase=Max("orders__created_at", filter=_purchase_condition()),
    )


def search_customers(options):
    customers = _customer_metrics(_base_customers(options)).order_by("-date_joined", "pk")[:500]
    return [_customer_row(customer) for customer in customers]


def _customer_row(customer):
    return {
        "id": customer.pk,
        "username": customer.username,
        "name": (f"{customer.first_name} {customer.last_name}").strip() or customer.username,
        "email": customer.email,
        "phone": customer.phone or "",
        "is_active": customer.is_active,
        "joined": timezone.localtime(customer.date_joined).isoformat(timespec="minutes"),
        "order_count": customer.order_count,
        "purchase_count": customer.purchase_count,
        "total_spent": format(customer.total_spent or Decimal("0.00"), ".2f"),
        "last_purchase": timezone.localtime(customer.last_purchase).isoformat(timespec="minutes") if customer.last_purchase else "",
    }


def customer_detail(customer_id=None, email=None):
    customer = _resolve_one_customer(customer_id=customer_id, email=email)
    metrics = _customer_metrics(User.objects.filter(pk=customer.pk)).get()
    return {
        "id": customer.pk,
        "username": customer.username,
        "first_name": customer.first_name,
        "last_name": customer.last_name,
        "email": customer.email,
        "phone": customer.phone or "",
        "is_active": customer.is_active,
        "is_staff": customer.is_staff,
        "is_superuser": customer.is_superuser,
        "date_joined": timezone.localtime(customer.date_joined).isoformat(timespec="minutes"),
        "last_login": timezone.localtime(customer.last_login).isoformat(timespec="minutes") if customer.last_login else "",
        "order_count": metrics.order_count,
        "paid_purchase_count": metrics.purchase_count,
        "total_spent": format(metrics.total_spent or Decimal("0.00"), ".2f"),
        "last_purchase": timezone.localtime(metrics.last_purchase).isoformat(timespec="minutes") if metrics.last_purchase else "",
    }


def _resolve_one_customer(customer_id=None, email=None):
    if bool(customer_id) == bool(email):
        raise CustomerManagerError("Fournissez exactement un --customer-id ou --email.")
    query = User.objects.all()
    if customer_id:
        query = query.filter(pk=customer_id)
    else:
        query = query.filter(email__iexact=email.strip())
    customer = query.first()
    if not customer or customer.is_staff or customer.is_superuser:
        raise CustomerManagerError("Compte client introuvable ou protege.")
    return customer


def customer_order_history(customer_id=None, email=None, order_status=None, date_from=None, date_to=None):
    customer = _resolve_one_customer(customer_id=customer_id, email=email)
    start, end = _date_bounds(date_from, date_to)
    orders = Order.objects.filter(user_id=customer.pk).prefetch_related("items").order_by("-created_at", "-pk")
    if order_status:
        orders = orders.filter(status=order_status)
    if start:
        orders = orders.filter(created_at__gte=start)
    if end:
        orders = orders.filter(created_at__lte=end)
    rows = []
    for order in orders:
        rows.append({
            "reference": order.reference,
            "created_at": timezone.localtime(order.created_at).isoformat(timespec="minutes"),
            "status": order.status,
            "payment_status": order.payment_status,
            "total": format(order.total, ".2f"),
            "items": [
                {"name": item.product_name, "quantity": item.quantity, "size": item.size,
                 "unit_price": format(item.unit_price, ".2f"), "line_total": format(item.line_total, ".2f")}
                for item in order.items.all().order_by("pk")
            ],
        })
    return {"customer_id": customer.pk, "orders": rows}


def customer_statistics(date_from=None, date_to=None):
    start, end = _date_bounds(date_from, date_to)
    customers = User.objects.filter(is_staff=False, is_superuser=False)
    new_customers = customers
    if start:
        new_customers = new_customers.filter(date_joined__gte=start)
    if end:
        new_customers = new_customers.filter(date_joined__lte=end)
    purchase_counts = customers.annotate(
        purchase_count=Count("orders", filter=_purchase_condition(), distinct=True)
    ).values_list("purchase_count", flat=True)
    activity = {"no_paid_orders": 0, "one_paid_order": 0, "two_to_five_paid_orders": 0, "six_or_more_paid_orders": 0}
    for count in purchase_counts:
        if count == 0:
            activity["no_paid_orders"] += 1
        elif count == 1:
            activity["one_paid_order"] += 1
        elif count <= 5:
            activity["two_to_five_paid_orders"] += 1
        else:
            activity["six_or_more_paid_orders"] += 1
    return {
        "total": customers.count(),
        "new_in_period": new_customers.count(),
        "active": customers.filter(is_active=True).count(),
        "suspended": customers.filter(is_active=False).count(),
        "with_orders": customers.filter(orders__isnull=False).distinct().count(),
        "activity": activity,
        "period": {"from": date_from or "", "to": date_to or ""},
    }


def top_customers(limit=20):
    customers = User.objects.filter(is_staff=False, is_superuser=False)
    # Purchase totals and ranking are lifetime metrics; optional dates select new accounts only in stats.
    ranked = _customer_metrics(customers).filter(purchase_count__gt=0).order_by("-total_spent", "-purchase_count", "pk")[:limit]
    return [_customer_row(customer) for customer in ranked]


def export_customers_csv(options):
    customers = _customer_metrics(_base_customers(options)).order_by("pk")
    output = io.StringIO(newline="")
    writer = csv.writer(output)
    writer.writerow(("customer_id", "username", "name", "email", "phone", "is_active", "date_joined", "order_count", "paid_purchase_count", "total_spent", "last_purchase"))
    for customer in customers:
        row = _customer_row(customer)
        writer.writerow((row["id"], row["username"], _csv_safe(row["name"]), _csv_safe(row["email"]), _csv_safe(row["phone"]), row["is_active"], row["joined"], row["order_count"], row["purchase_count"], row["total_spent"], row["last_purchase"]))
    return "\ufeff" + output.getvalue()


def export_customer_orders_csv(customer_id=None, email=None, order_status=None, date_from=None, date_to=None):
    history = customer_order_history(customer_id, email, order_status, date_from, date_to)
    output = io.StringIO(newline="")
    writer = csv.writer(output)
    writer.writerow(("customer_id", "order_reference", "created_at", "order_status", "payment_status", "total", "items"))
    for order in history["orders"]:
        items = "; ".join(f"{item['quantity']}x {item['name']} [{item['unit_price']}]" for item in order["items"])
        writer.writerow((history["customer_id"], order["reference"], order["created_at"], order["status"], order["payment_status"], order["total"], _csv_safe(items)))
    return "\ufeff" + output.getvalue()


def _csv_safe(value):
    text = str(value or "")
    return "'" + text if text[:1] in ("=", "+", "-", "@", "\t", "\r") else text


def _customer_version(customer):
    return {
        "id": customer.pk,
        "username": customer.username,
        "email": customer.email,
        "first_name": customer.first_name,
        "last_name": customer.last_name,
        "phone": customer.phone or "",
        "is_active": customer.is_active,
        "is_staff": customer.is_staff,
        "is_superuser": customer.is_superuser,
        "date_joined": customer.date_joined.isoformat(),
    }


def _read_selection(options):
    ids = options.get("customer_ids") or []
    emails = options.get("emails") or []
    csv_path = options.get("csv")
    errors, source = [], {}
    if csv_path:
        if ids or emails:
            raise CustomerManagerError("Utilisez --csv ou --customer-id/--email, pas les deux.")
        path = Path(csv_path)
        try:
            if path.stat().st_size > MAX_CUSTOMER_CSV_BYTES:
                raise CustomerManagerError("CSV trop volumineux (maximum 10 Mo).")
            raw = path.read_bytes()
            text = raw.decode("utf-8-sig")
        except CustomerManagerError:
            raise
        except (OSError, UnicodeDecodeError) as exc:
            raise CustomerManagerError(f"CSV illisible ({type(exc).__name__}).") from None
        try:
            dialect = csv.Sniffer().sniff(text[:4096], delimiters=",;\t")
        except csv.Error:
            dialect = csv.excel
        reader = csv.DictReader(io.StringIO(text, newline=""), dialect=dialect)
        if not reader.fieldnames:
            raise CustomerManagerError("CSV sans en-tetes.")
        field = next((header for header in reader.fieldnames if header and header.strip().lower() in ("id", "customer_id", "email")), None)
        if not field:
            raise CustomerManagerError("Le CSV doit avoir une colonne id/customer_id ou email.")
        for row in reader:
            value = (row.get(field) or "").strip()
            if not value:
                errors.append(f"CSV ligne {reader.line_num}: identifiant vide.")
            elif field.strip().lower() in ("id", "customer_id"):
                try:
                    ids.append(int(value))
                except ValueError:
                    errors.append(f"CSV ligne {reader.line_num}: identifiant invalide.")
            else:
                emails.append(value)
            if len(ids) + len(emails) > MAX_CUSTOMER_BATCH:
                raise CustomerManagerError("Maximum 1 000 comptes par lot.")
        source = {"csv_sha256": hashlib.sha256(raw).hexdigest(), "source_filename": path.name}
    if bool(ids) and bool(emails):
        raise CustomerManagerError("Utilisez des identifiants ou des e-mails, pas les deux.")
    if len(ids) + len(emails) == 0:
        raise CustomerManagerError("Fournissez un --customer-id, un --email ou un --csv.")
    if len(ids) + len(emails) > MAX_CUSTOMER_BATCH:
        raise CustomerManagerError("Maximum 1 000 comptes par lot.")
    if len(ids) != len(set(ids)) or len([email.lower() for email in emails]) != len(set(email.lower() for email in emails)):
        errors.append("Le lot contient des identifiants en double.")
    if ids:
        query = User.objects.filter(pk__in=ids)
    else:
        email_query = Q()
        for email in emails:
            email_query |= Q(email__iexact=email)
        query = User.objects.filter(email_query)
    found = list(query.order_by("pk"))
    lookup = {customer.pk: customer for customer in found} if ids else {}
    ambiguous_emails = set()
    if not ids:
        for customer in found:
            key = customer.email.lower()
            if key in lookup:
                ambiguous_emails.add(key)
            lookup[key] = customer
    wanted = ids if ids else emails
    customers = []
    for key in wanted:
        if not ids and key.lower() in ambiguous_emails:
            errors.append(f"E-mail ambigu pour plusieurs comptes: {key.lower()}.")
            continue
        customer = lookup.get(key if ids else key.lower())
        if not customer:
            errors.append(f"Compte introuvable: {key if ids else key.lower()}.")
        elif customer.is_staff or customer.is_superuser:
            errors.append(f"Compte privilegie protege (ID {customer.pk}); aucune modification autorisee.")
        else:
            customers.append(customer)
    return customers, errors, source


def _fingerprint(operation, params, rows, source, errors):
    payload = {
        "version": 1,
        "operation": operation,
        "params": params,
        "source": source,
        "rows": [{"id": row.customer_id, "version": row.version, "before": row.before, "after": row.after, "errors": row.errors} for row in rows],
        "errors": errors,
    }
    return hashlib.sha256(_json(payload).encode("utf-8")).hexdigest()


def build_customer_change_plan(action, options):
    if action not in ("suspend", "reactivate"):
        raise CustomerManagerError(f"Action de compte inconnue: {action}.")
    reason = (options.get("reason") or "").strip()
    errors = []
    if action == "suspend" and (len(reason) < 5 or len(reason) > 500):
        errors.append("Une raison de suspension est obligatoire (5 a 500 caracteres).")
    elif reason and len(reason) > 500:
        errors.append("La raison ne peut pas depasser 500 caracteres.")
    customers, selection_errors, source = _read_selection(options)
    errors.extend(selection_errors)
    desired = action == "reactivate"
    rows = []
    for customer in customers:
        row_errors = []
        if customer.is_staff or customer.is_superuser:
            row_errors.append("Compte administrateur/superutilisateur protege.")
        before = {"is_active": customer.is_active}
        after = {"is_active": desired}
        if customer.is_active != desired and reason:
            after["reason"] = reason
        rows.append(CustomerPlanRow(
            customer.pk,
            f"{customer.get_full_name().strip() or customer.username} <{customer.email}>",
            _customer_version(customer), before, after, row_errors,
        ))
    params = {"reason": reason, "active_after": desired}
    errors.extend(f"{row.label}: {message}" for row in rows for message in row.errors)
    fingerprint = _fingerprint(action, params, rows, source, errors)
    return CustomerChangePlan(action, params, rows, errors, fingerprint)


def format_customer_plan(plan):
    lines = [f"PREVIEW {plan.operation}", f"Empreinte a approuver: {plan.fingerprint}", f"Comptes concernes: {len(plan.rows)}"]
    for row in plan.rows:
        lines.append(f"ID={row.customer_id} | {row.label} | actif={row.before['is_active']} -> {row.after['is_active']}")
        if "reason" in row.after:
            lines.append(f"  raison: {row.after['reason']}")
        lines.extend(f"  ERREUR: {error}" for error in row.errors)
    lines.extend(f"CONFLIT: {error}" for error in plan.errors)
    lines.append("Aucune ecriture en PREVIEW.")
    if plan.operation == "suspend":
        lines.append("La suspension empechera les prochaines authentifications; APPLY exige --confirm-suspension SUSPENDRE.")
    return "\n".join(lines)


def apply_customer_change_plan(plan):
    if plan.errors:
        raise CustomerManagerError("L'apercu contient des erreurs; aucun compte modifie.")
    if not plan.rows:
        raise CustomerManagerError("Aucun compte a modifier.")
    if not plan.has_changes:
        return {"status": "no_changes", "fingerprint": plan.fingerprint, "changed": 0, "already_applied": False}
    try:
        with transaction.atomic():
            existing = CustomerChangeBatch.objects.select_for_update().filter(fingerprint=plan.fingerprint).first()
            if existing:
                return {"status": existing.status, "fingerprint": existing.fingerprint, "changed": existing.changes.count(), "already_applied": True}
            customers = {
                user.pk: user
                for user in User.objects.select_for_update().filter(pk__in=[row.customer_id for row in plan.rows]).order_by("pk")
            }
            for row in plan.rows:
                customer = customers.get(row.customer_id)
                if not customer or _customer_version(customer) != row.version:
                    raise StaleCustomerPlan(f"Compte client ID {row.customer_id} modifie depuis PREVIEW.")
                if customer.is_staff or customer.is_superuser:
                    raise StaleCustomerPlan(f"Compte privilegie ID {row.customer_id}; APPLY refuse.")
            batch = CustomerChangeBatch.objects.create(
                fingerprint=plan.fingerprint,
                operation=plan.operation,
                details={"count": len(plan.rows), "reason_present": bool(plan.params["reason"])},
            )
            for row in plan.rows:
                customer = customers[row.customer_id]
                customer.is_active = row.after["is_active"]
                customer.save(update_fields=("is_active",))
                CustomerChangeLog.objects.create(
                    batch=batch,
                    customer=customer,
                    customer_id_snapshot=customer.pk,
                    before=row.before,
                    after=row.after,
                )
        return {"status": "applied", "fingerprint": plan.fingerprint, "changed": len(plan.rows), "already_applied": False}
    except IntegrityError:
        existing = CustomerChangeBatch.objects.filter(fingerprint=plan.fingerprint).first()
        if existing:
            return {"status": existing.status, "fingerprint": existing.fingerprint, "changed": existing.changes.count(), "already_applied": True}
        raise


def format_customers(rows):
    lines = [f"Clients: {len(rows)}" + (" (maximum 500 affiches)." if len(rows) == 500 else ".")]
    for row in rows:
        lines.append(f"ID={row['id']} | {row['name']} | {row['email']} | telephone={row['phone'] or '-'} | {'actif' if row['is_active'] else 'suspendu'} | commandes={row['order_count']} | depenses={row['total_spent']} | dernier achat={row['last_purchase'] or '-'}")
    return "\n".join(lines)


def format_customer_detail(detail):
    return "\n".join((
        f"Client ID={detail['id']} | {detail['first_name']} {detail['last_name']} | {detail['email']} | {detail['phone'] or '-'}",
        f"Compte: {'actif' if detail['is_active'] else 'suspendu'} | cree={detail['date_joined']} | derniere connexion={detail['last_login'] or '-'}",
        f"Commandes={detail['order_count']} | achats payes={detail['paid_purchase_count']} | depenses={detail['total_spent']} | dernier achat={detail['last_purchase'] or '-'}",
    ))


def format_history(history):
    lines = [f"Historique des commandes | client ID={history['customer_id']} | {len(history['orders'])} commande(s)"]
    for order in history["orders"]:
        lines.append(f"{order['reference']} | {order['created_at']} | {order['status']} / paiement={order['payment_status']} | total={order['total']}")
        lines.extend(f"  {item['quantity']} x {item['name']} | taille={item['size'] or '-'} | {item['unit_price']} = {item['line_total']}" for item in order["items"])
    return "\n".join(lines)


def format_statistics(stats):
    activity = stats["activity"]
    return "\n".join((
        f"Clients total={stats['total']} | nouveaux periode={stats['new_in_period']} | actifs={stats['active']} | suspendus={stats['suspended']} | avec commande={stats['with_orders']}",
        "Activite (achats payes, hors annulations): " + _json(activity),
        f"Periode de creation: {stats['period']['from'] or '-'} -> {stats['period']['to'] or '-'}",
    ))


def get_customer_change_history(fingerprint):
    batch = CustomerChangeBatch.objects.filter(fingerprint=fingerprint.strip().lower()).first()
    if not batch:
        raise CustomerManagerError("Journal introuvable pour cette empreinte.")
    rows = list(batch.changes.values("customer_id_snapshot", "before", "after"))
    return batch, rows


def format_audit(batch, rows):
    lines = [f"{batch.operation} | {batch.status} | {batch.created_at.isoformat()} | {batch.fingerprint}"]
    lines.extend(f"Client ID={row['customer_id_snapshot']} | avant={_json(row['before'])} | apres={_json(row['after'])}" for row in rows)
    return "\n".join(lines)
