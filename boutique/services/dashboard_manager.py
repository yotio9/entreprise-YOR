"""Read-only business metrics for Yorwani, based on recorded orders and snapshots."""

import csv
import io
import json
from datetime import date, datetime, time, timedelta
from decimal import Decimal

from django.db.models import Count, DecimalField, ExpressionWrapper, F, Q, Sum, Value
from django.db.models.functions import Coalesce, Greatest, TruncDay, TruncMonth, TruncWeek
from django.utils import timezone

from boutique.models import Category, Order, OrderItem, Product, User

ZERO = Decimal("0.00")
NON_CANCELLED = ~Q(status=Order.Status.CANCELLED)
CONFIRMED = Q(payment_status=Order.PaymentStatus.CONFIRMED)
UNPAID = Q(payment_status__in=(Order.PaymentStatus.PENDING, Order.PaymentStatus.DUE_ON_DELIVERY))


class DashboardManagerError(Exception):
    pass


def _aware_boundary(day, end=False):
    value = datetime.combine(day, time.min)
    if end:
        value += timedelta(days=1)
    return timezone.make_aware(value, timezone.get_current_timezone())


def period_bounds(period="all", date_from=None, date_to=None, now=None):
    now = timezone.localtime(now or timezone.now())
    today = now.date()
    if date_from or date_to:
        if not date_from or not date_to:
            raise DashboardManagerError("Fournissez date-from et date-to ensemble.")
        try:
            start_day = date.fromisoformat(date_from)
            end_day = date.fromisoformat(date_to)
        except ValueError:
            raise DashboardManagerError("Dates attendues au format YYYY-MM-DD.") from None
        if start_day > end_day:
            raise DashboardManagerError("date-from doit etre inferieure ou egale a date-to.")
        return _aware_boundary(start_day), _aware_boundary(end_day, end=True), start_day.isoformat(), end_day.isoformat()
    if period == "all":
        return None, None, "", ""
    if period == "day":
        start_day, end_day = today, today
    elif period == "week":
        start_day = today - timedelta(days=today.weekday())
        end_day = start_day + timedelta(days=6)
    elif period == "month":
        start_day = today.replace(day=1)
        if today.month == 12:
            next_month = today.replace(year=today.year + 1, month=1, day=1)
        else:
            next_month = today.replace(month=today.month + 1, day=1)
        end_day = next_month - timedelta(days=1)
    else:
        raise DashboardManagerError("period doit valoir day, week, month, all, ou etre defini avec les deux dates.")
    return _aware_boundary(start_day), _aware_boundary(end_day, end=True), start_day.isoformat(), end_day.isoformat()


def _period_filter(queryset, field, start, end):
    if start:
        queryset = queryset.filter(**{f"{field}__gte": start})
    if end:
        queryset = queryset.filter(**{f"{field}__lt": end})
    return queryset


def _net_total_expression(prefix=""):
    def col(name):
        return f"{prefix}{name}"
    return Greatest(
        F(col("subtotal")) + F(col("shipping_fee")) - F(col("discount_amount")),
        Value(ZERO),
        output_field=DecimalField(max_digits=12, decimal_places=2),
    )


def _sum_order_totals(orders):
    return orders.aggregate(total=Sum(_net_total_expression()))["total"] or ZERO


def _money(value):
    return format(value or ZERO, ".2f")


def _orders(start=None, end=None, realized_only=False):
    queryset = Order.objects.all()
    if realized_only:
        queryset = queryset.filter(NON_CANCELLED & CONFIRMED)
    return _period_filter(queryset, "created_at", start, end)


def _eligible_sales_orders(start=None, end=None):
    return _orders(start, end, realized_only=True)


def _order_item_snapshot_check(orders):
    item_total = ExpressionWrapper(
        F("items__quantity") * F("items__unit_price"),
        output_field=DecimalField(max_digits=24, decimal_places=2),
    )
    mismatch_query = orders.annotate(
        snapshot_subtotal=Coalesce(Sum(item_total), Value(ZERO), output_field=DecimalField(max_digits=24, decimal_places=2)),
        item_count=Count("items", distinct=True),
    ).filter(Q(item_count=0) | ~Q(snapshot_subtotal=F("subtotal"))).order_by("pk")
    samples = list(mismatch_query.values("reference", "subtotal", "snapshot_subtotal", "item_count")[:20])
    return {
        "mismatched_or_missing_orders": mismatch_query.count(),
        "sample_references": samples,
        "basis": "Order.subtotal reste la source du chiffre financier; ces alertes signalent l'absence d'articles ou une difference avec les snapshots OrderItem.",
    }


def _period_data(period="all", date_from=None, date_to=None):
    start, end, label_from, label_to = period_bounds(period, date_from, date_to)
    return start, end, {"period": period if not (date_from or date_to) else "custom", "from": label_from, "to": label_to}


def revenue_summary(start=None, end=None):
    all_orders = _period_filter(Order.objects.all(), "created_at", start, end)
    non_cancelled = all_orders.filter(NON_CANCELLED)
    sales = non_cancelled.filter(CONFIRMED)
    unpaid = non_cancelled.filter(UNPAID)
    cancelled = all_orders.filter(status=Order.Status.CANCELLED)
    payment_distribution = {}
    for code, label in Order.PaymentStatus.choices:
        bucket = all_orders.filter(payment_status=code)
        active_bucket = bucket.filter(NON_CANCELLED)
        cancelled_bucket = bucket.filter(status=Order.Status.CANCELLED)
        payment_distribution[code] = {
            "label": label,
            "orders_non_cancelled": active_bucket.count(),
            "amount_non_cancelled": _money(_sum_order_totals(active_bucket)),
            "orders_cancelled": cancelled_bucket.count(),
            "amount_cancelled": _money(_sum_order_totals(cancelled_bucket)),
        }
    cancelled_confirmed = cancelled.filter(CONFIRMED)
    return {
        "sales_realized_paid_non_cancelled": _money(_sum_order_totals(sales)),
        "sales_orders_paid_non_cancelled": sales.count(),
        "payments_confirmed_recorded_including_cancelled": _money(_sum_order_totals(all_orders.filter(CONFIRMED))),
        "cancelled_orders_with_confirmed_payment_not_refund_adjusted": _money(_sum_order_totals(cancelled_confirmed)),
        "amount_awaiting_payment_non_cancelled": _money(_sum_order_totals(unpaid)),
        "orders_awaiting_payment_non_cancelled": unpaid.count(),
        "payment_status_distribution": payment_distribution,
        "refund_status_available": False,
        "note": "Les statuts de paiement refletent les valeurs en base. Aucun statut de remboursement n'existe; les montants payes puis annules ne sont pas ajustes des remboursements.",
    }


def overview(period="month", date_from=None, date_to=None):
    now = timezone.localtime()
    today_start = _aware_boundary(now.date())
    tomorrow = _aware_boundary(now.date(), end=True)
    week_start_day = now.date() - timedelta(days=now.weekday())
    week_start = _aware_boundary(week_start_day)
    month_start = _aware_boundary(now.date().replace(day=1))
    start, end, period_meta = _period_data(period, date_from, date_to)
    all_orders = Order.objects.all()
    active_orders = all_orders.filter(NON_CANCELLED)
    status_counts = {code: all_orders.filter(status=code).count() for code, _ in Order.Status.choices}
    customers = User.objects.filter(is_staff=False, is_superuser=False)
    new_customers = _period_filter(customers, "date_joined", start, end)
    active_products = Product.objects.filter(is_active=True)
    stock_value = active_products.aggregate(value=Sum(ExpressionWrapper(F("price") * F("stock"), output_field=DecimalField(max_digits=30, decimal_places=2))))["value"] or ZERO
    out_of_stock = active_products.filter(stock=0)
    sales_today = _eligible_sales_orders(today_start, tomorrow)
    sales_week = _eligible_sales_orders(week_start, tomorrow)
    sales_month = _eligible_sales_orders(month_start, tomorrow)
    return {
        "as_of": now.isoformat(timespec="seconds"),
        "sales_period": period_meta,
        "revenue_realized_paid_non_cancelled": _money(_sum_order_totals(active_orders.filter(CONFIRMED))),
        "revenue_today": _money(_sum_order_totals(sales_today)),
        "revenue_this_week": _money(_sum_order_totals(sales_week)),
        "revenue_this_month": _money(_sum_order_totals(sales_month)),
        "orders_total": all_orders.count(),
        "orders_by_status": status_counts,
        "orders_pending_payment": active_orders.filter(UNPAID).count(),
        "customers_total": customers.count(),
        "new_customers_in_period": new_customers.count(),
        "customer_period": period_meta,
        "active_products": active_products.count(),
        "active_products_out_of_stock": out_of_stock.count(),
        "active_products_low_stock_1_to_5": active_products.filter(stock__gt=0, stock__lte=5).count(),
        "active_inventory_units": active_products.aggregate(value=Sum("stock"))["value"] or 0,
        "active_inventory_retail_value_estimate": _money(stock_value),
        "inventory_valuation_basis": "Quantite en stock x prix de vente actuel; ce n'est pas une valeur au cout d'achat.",
        "purchase_cost_fields_available": False,
        "margins_calculated": False,
        "pending_sales_amount_in_period": _money(_sum_order_totals(_period_filter(active_orders.filter(UNPAID), "created_at", start, end))),
    }


def sales_analysis(period="month", date_from=None, date_to=None, granularity="day"):
    start, end, meta = _period_data(period, date_from, date_to)
    sales_orders = _eligible_sales_orders(start, end)
    total_revenue = _sum_order_totals(sales_orders)
    order_count = sales_orders.count()
    item_filter = _period_filter(OrderItem.objects.filter(order__status__in=[value for value, _ in Order.Status.choices if value != Order.Status.CANCELLED], order__payment_status=Order.PaymentStatus.CONFIRMED), "order__created_at", start, end)
    units = item_filter.aggregate(value=Sum("quantity"))["value"] or 0
    trunc = {"day": TruncDay, "week": TruncWeek, "month": TruncMonth}.get(granularity)
    if not trunc:
        raise DashboardManagerError("granularity doit valoir day, week ou month.")
    tz = timezone.get_current_timezone()
    order_series = sales_orders.annotate(bucket=trunc("created_at", tzinfo=tz)).values("bucket").annotate(
        revenue=Sum(_net_total_expression()), orders=Count("pk")
    ).order_by("bucket")
    item_series = item_filter.annotate(bucket=trunc("order__created_at", tzinfo=tz)).values("bucket").annotate(units=Sum("quantity")).order_by("bucket")
    series = {}
    for row in order_series:
        key = timezone.localtime(row["bucket"]).isoformat() if timezone.is_aware(row["bucket"]) else row["bucket"].isoformat()
        series[key] = {"date": key, "revenue": _money(row["revenue"]), "orders": row["orders"], "units_sold": 0}
    for row in item_series:
        key = timezone.localtime(row["bucket"]).isoformat() if timezone.is_aware(row["bucket"]) else row["bucket"].isoformat()
        series.setdefault(key, {"date": key, "revenue": "0.00", "orders": 0, "units_sold": 0})["units_sold"] = row["units"]
    return {
        **meta,
        "revenue_realized_paid_non_cancelled": _money(total_revenue),
        "paid_non_cancelled_orders": order_count,
        "items_sold_paid_non_cancelled": units,
        "average_basket": _money(total_revenue / order_count if order_count else ZERO),
        "granularity": granularity,
        "series": list(series.values()),
        "order_item_snapshot_check": _order_item_snapshot_check(sales_orders),
        "note": "Les ventes incluent uniquement les commandes non annulees dont payment_status est confirme. Les prix d'articles proviennent des snapshots OrderItem.",
    }


def _item_rollups(start=None, end=None):
    items = OrderItem.objects.filter(order__status__in=[value for value, _ in Order.Status.choices if value != Order.Status.CANCELLED], order__payment_status=Order.PaymentStatus.CONFIRMED)
    items = _period_filter(items, "order__created_at", start, end)
    return list(items.values("product_id", "product_name").annotate(
        units=Sum("quantity"), line_revenue=Sum(ExpressionWrapper(F("quantity") * F("unit_price"), output_field=DecimalField(max_digits=20, decimal_places=2)))
    ))


def _product_sales(start=None, end=None):
    rollups = _item_rollups(start, end)
    product_ids = {row["product_id"] for row in rollups if row["product_id"] is not None}
    products = Product.objects.select_related("category").in_bulk(product_ids)
    grouped = {}
    for row in rollups:
        product_id = row["product_id"]
        product = products.get(product_id)
        key = ("product", product_id) if product else ("historical", row["product_name"])
        entry = grouped.setdefault(key, {
            "product_id": product_id if product else None,
            "product": product.name if product else f"Article historique sans produit lie: {row['product_name']}",
            "historical_names": set(),
            "collection_id": product.category_id if product else None,
            "collection": product.category.name if product else "Non attribuee (produit supprime)",
            "units_sold": 0,
            "line_revenue_at_historical_prices": ZERO,
        })
        entry["historical_names"].add(row["product_name"])
        entry["units_sold"] += row["units"] or 0
        entry["line_revenue_at_historical_prices"] += row["line_revenue"] or ZERO
    for entry in grouped.values():
        entry["historical_names"] = sorted(entry["historical_names"])
        entry["line_revenue_at_historical_prices"] = _money(entry["line_revenue_at_historical_prices"])
    return list(grouped.values())


def top_products(limit=10, sort="best", period="all", date_from=None, date_to=None):
    if not 1 <= limit <= 100:
        raise DashboardManagerError("limit doit etre compris entre 1 et 100.")
    start, end, meta = _period_data(period, date_from, date_to)
    sold = _product_sales(start, end)
    if sort == "least":
        sold_ids = {row["product_id"] for row in sold if row["product_id"]}
        for product in Product.objects.filter(is_active=True).select_related("category"):
            if product.pk not in sold_ids:
                sold.append({"product_id": product.pk, "product": product.name, "historical_names": [], "collection_id": product.category_id, "collection": product.category.name, "units_sold": 0, "line_revenue_at_historical_prices": "0.00"})
        sold.sort(key=lambda row: (row["units_sold"], row["product"].casefold(), row["product_id"] or 0))
    elif sort == "best":
        sold.sort(key=lambda row: (-row["units_sold"], row["product"].casefold(), row["product_id"] or 0))
    else:
        raise DashboardManagerError("sort doit valoir best ou least.")
    return {**meta, "sort": sort, "basis": "Quantites des OrderItem des commandes payees confirmees non annulees; revenu de ligne au prix historique hors repartition des remises/livraison.", "products": sold[:limit]}


def top_collections(limit=10, period="all", date_from=None, date_to=None):
    if not 1 <= limit <= 1000:
        raise DashboardManagerError("limit doit etre compris entre 1 et 1 000.")
    start, end, meta = _period_data(period, date_from, date_to)
    products = _product_sales(start, end)
    grouped = {}
    unattached_units = 0
    for row in products:
        if row["collection_id"] is None:
            unattached_units += row["units_sold"]
            continue
        entry = grouped.setdefault(row["collection_id"], {"collection_id": row["collection_id"], "collection": row["collection"], "units_sold": 0, "line_revenue_at_historical_prices": ZERO, "products_sold": 0})
        entry["units_sold"] += row["units_sold"]
        entry["line_revenue_at_historical_prices"] += Decimal(row["line_revenue_at_historical_prices"])
        if row["units_sold"]:
            entry["products_sold"] += 1
    rows = list(grouped.values())
    for row in rows:
        row["line_revenue_at_historical_prices"] = _money(row["line_revenue_at_historical_prices"])
    rows.sort(key=lambda row: (-row["units_sold"], row["collection"].casefold()))
    return {**meta, "collections": rows[:limit], "units_without_current_collection": unattached_units,
            "attribution_note": "La categorie utilise la collection actuelle du produit; OrderItem ne conserve pas la categorie au moment de la vente."}


def inventory_report():
    products = Product.objects.filter(is_active=True)
    data = products.aggregate(
        products=Count("pk"),
        units=Sum("stock"),
        out_of_stock=Count("pk", filter=Q(stock=0)),
        retail_value=Sum(ExpressionWrapper(F("price") * F("stock"), output_field=DecimalField(max_digits=30, decimal_places=2))),
    )
    return {
        "active_products": data["products"] or 0,
        "units_in_stock": data["units"] or 0,
        "out_of_stock_products": data["out_of_stock"] or 0,
        "inventory_retail_value_estimate": _money(data["retail_value"]),
        "valuation_basis": "Stock des produits actifs x prix de vente actuel; estimation de valeur de vente, pas cout d'achat.",
        "purchase_cost_available": False,
        "margins_available": False,
    }


def compare_periods(period="month", date_from=None, date_to=None, compare_from=None, compare_to=None):
    current_start, current_end, current_meta = _period_data(period, date_from, date_to)
    if not current_start or not current_end:
        raise DashboardManagerError("compare exige period day/week/month ou date-from/date-to.")
    if bool(compare_from) != bool(compare_to):
        raise DashboardManagerError("Fournissez compare-from et compare-to ensemble.")
    if compare_from:
        previous_start, previous_end, previous_meta = _period_data("custom", compare_from, compare_to)
    elif not date_from and not date_to and period == "month":
        this_month = current_start.date()
        last_month_end_day = this_month - timedelta(days=1)
        last_month_start_day = last_month_end_day.replace(day=1)
        previous_start = _aware_boundary(last_month_start_day)
        previous_end = current_start
        previous_meta = {"period": "previous_calendar_month", "from": last_month_start_day.isoformat(), "to": last_month_end_day.isoformat()}
    else:
        duration = current_end - current_start
        previous_end = current_start
        previous_start = current_start - duration
        previous_meta = {"period": "preceding_equal_duration", "from": previous_start.date().isoformat(), "to": (previous_end - timedelta(microseconds=1)).date().isoformat()}
    def metrics(start, end):
        orders = _eligible_sales_orders(start, end)
        revenue = _sum_order_totals(orders)
        count = orders.count()
        items = OrderItem.objects.filter(order__in=orders).aggregate(value=Sum("quantity"))["value"] or 0
        return {"realized_revenue": _money(revenue), "paid_orders": count, "items_sold": items, "average_basket": _money(revenue / count if count else ZERO)}
    current = metrics(current_start, current_end)
    previous = metrics(previous_start, previous_end)
    variation = {}
    for key in current:
        current_value, previous_value = Decimal(str(current[key])), Decimal(str(previous[key]))
        variation[key] = None if previous_value == 0 else format((current_value - previous_value) / previous_value * 100, ".2f")
    return {"current_period": current_meta, "previous_period": previous_meta, "current": current, "previous": previous, "variation_percent": variation, "note": "Pourcentage non defini (null) si la periode de comparaison vaut zero."}


def product_report(product_id=None, period="all", date_from=None, date_to=None, limit=100):
    if not 1 <= limit <= 1000:
        raise DashboardManagerError("limit doit etre compris entre 1 et 1 000.")
    start, end, meta = _period_data(period, date_from, date_to)
    rows = _product_sales(start, end)
    if product_id:
        rows = [row for row in rows if row["product_id"] == product_id]
        if not rows:
            product = Product.objects.filter(pk=product_id).first()
            if not product:
                raise DashboardManagerError(f"Produit ID {product_id} introuvable.")
            rows = [{"product_id": product.pk, "product": product.name, "historical_names": [], "collection_id": product.category_id, "collection": product.category.name, "units_sold": 0, "line_revenue_at_historical_prices": "0.00"}]
    else:
        rows.sort(key=lambda row: (-row["units_sold"], row["product"].casefold()))
        rows = rows[:limit]
    return {"period": meta, "products": rows}


def collection_report(collection=None, period="all", date_from=None, date_to=None, limit=100):
    result = top_collections(limit=1000, period=period, date_from=date_from, date_to=date_to)
    rows = result["collections"]
    if collection:
        category = Category.objects.filter(name__iexact=collection.strip()).first()
        if not category:
            raise DashboardManagerError(f"Collection introuvable: {collection}.")
        rows = [row for row in rows if row["collection_id"] == category.pk]
        if not rows:
            rows = [{"collection_id": category.pk, "collection": category.name, "units_sold": 0, "line_revenue_at_historical_prices": "0.00", "products_sold": 0}]
    return {"period": {key: result[key] for key in ("period", "from", "to")}, "collections": rows, "attribution_note": result["attribution_note"]}


def report(report_type, period="all", date_from=None, date_to=None, granularity="day", product_id=None, collection=None, limit=100):
    if report_type in ("daily", "weekly", "monthly"):
        default_period = {"daily": "day", "weekly": "week", "monthly": "month"}[report_type]
        selected_period = "custom" if date_from or date_to else default_period
        granularity = {"daily": "day", "weekly": "week", "monthly": "month"}[report_type]
        return sales_analysis(selected_period, date_from, date_to, granularity=granularity)
    if report_type == "product":
        return product_report(product_id, period, date_from, date_to, limit)
    if report_type == "collection":
        return collection_report(collection, period, date_from, date_to, limit)
    raise DashboardManagerError("report-type doit valoir daily, weekly, monthly, product ou collection.")


def csv_export(data):
    output = io.StringIO(newline="")
    writer = csv.writer(output)
    writer.writerow(("section", "metric", "value", "record_json"))
    def add(section, key, value):
        text = str(value)
        if text[:1] in ("=", "+", "-", "@", "\t", "\r"):
            text = "'" + text
        writer.writerow((section, key, text, ""))
    def walk(section, value, prefix=""):
        if isinstance(value, dict):
            for key, child in value.items():
                walk(section, child, f"{prefix}.{key}" if prefix else str(key))
        elif isinstance(value, list):
            for index, row in enumerate(value):
                record = json.dumps(row, ensure_ascii=False, sort_keys=True, default=str)
                if record[:1] in ("=", "+", "-", "@", "\t", "\r"):
                    record = "'" + record
                writer.writerow((section, f"{prefix}[{index}]", "", record))
        else:
            add(section, prefix, value)
    walk("dashboard", data)
    return "\ufeff" + output.getvalue()


def format_dashboard(data):
    return json.dumps(data, ensure_ascii=False, indent=2, default=str)
