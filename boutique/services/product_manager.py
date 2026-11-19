"""Preview-first product operations with optimistic locking and an audit trail."""

import csv
import hashlib
import io
import json
import re
import warnings
from dataclasses import asdict, dataclass, field
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from pathlib import Path

from django.core.files.base import ContentFile
from django.core.files.storage import default_storage
from django.db import IntegrityError, transaction
from django.db.models import Q
from PIL import Image, UnidentifiedImageError

from boutique.models import Category, Product, ProductChangeBatch, ProductChangeLog


MAX_CSV_BYTES = 25 * 1024 * 1024
MAX_ROWS = 5000
MAX_IMAGE_BYTES = 20 * 1024 * 1024
MAX_PRODUCT_PRICE = Decimal("9999999999.99")
IMAGE_FORMATS = {"JPEG": ".jpg", "PNG": ".png", "WEBP": ".webp"}
CSV_FIELDS = {
    "id": "id",
    "product_id": "id",
    "sku": "sku",
    "name": "name",
    "nom": "name",
    "nom_du_produit": "name",
    "description": "description",
    "price": "price",
    "prix": "price",
    "stock": "stock",
    "collection": "collection",
    "category": "collection",
    "categorie": "collection",
    "image_filename": "image_filename",
    "image": "image_filename",
    "is_active": "is_active",
    "published": "is_active",
    "publication": "is_active",
}
EDITABLE_CSV_FIELDS = {"name", "description", "price", "stock", "collection", "image_filename"}


class ProductManagerError(Exception):
    pass


class StaleProductPlan(ProductManagerError):
    pass


@dataclass
class ChangeRow:
    product_id: int
    sku: str
    label: str
    version: dict
    changes: dict = field(default_factory=dict)
    errors: list[str] = field(default_factory=list)
    assets: dict = field(default_factory=dict)


@dataclass
class CategoryChange:
    category_id: int
    label: str
    version: dict
    before: dict
    after: dict


@dataclass
class ProductChangePlan:
    operation: str
    params: dict
    rows: list[ChangeRow]
    category_changes: list[CategoryChange]
    errors: list[str]
    fingerprint: str

    @property
    def has_changes(self):
        return any(row.changes for row in self.rows) or bool(self.category_changes)


def _json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)


def _header(value):
    key = (value or "").strip().lower().replace(" ", "_").replace("-", "_")
    return CSV_FIELDS.get(key, key)


def _product_version(product):
    return {
        "sku": product.sku or "",
        "name": product.name,
        "description": product.description,
        "price": format(product.price, ".2f"),
        "stock": product.stock,
        "category_id": product.category_id,
        "category_name": product.category.name,
        "image": product.image.name if product.image else "",
        "image_url": product.image_url,
        "is_active": product.is_active,
        "updated_at": product.updated_at.isoformat(),
    }


def _category_version(category):
    return {
        "name": category.name,
        "slug": category.slug,
        "description": category.description,
        "image": category.image.name if category.image else "",
        "is_active": category.is_active,
    }


def _category_lookup(name):
    matches = list(Category.objects.filter(name__iexact=name.strip())[:2])
    if len(matches) != 1:
        if not matches:
            raise ProductManagerError(f"Collection introuvable : {name}.")
        raise ProductManagerError(f"Nom de collection ambigu : {name}.")
    return matches[0]


def _resolve_image(images_dir, filename):
    if not images_dir:
        raise ProductManagerError("--images est obligatoire avec image_filename.")
    candidate = Path(filename.strip())
    if not filename.strip() or candidate.is_absolute() or candidate.name != filename.strip() or "/" in filename or "\\" in filename:
        raise ProductManagerError("Le nom d'image doit désigner un fichier directement dans --images.")
    root = Path(images_dir).resolve(strict=True)
    path = (root / candidate).resolve(strict=True)
    if path.parent != root or not path.is_file():
        raise ProductManagerError("Fichier image introuvable ou hors du dossier fourni.")
    if path.stat().st_size > MAX_IMAGE_BYTES:
        raise ProductManagerError("Image trop volumineuse (maximum 20 Mo).")
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(path) as image:
                image_format = image.format
                image.verify()
    except (UnidentifiedImageError, OSError, Image.DecompressionBombError, Image.DecompressionBombWarning):
        raise ProductManagerError("Image invalide ou non prise en charge.") from None
    if image_format not in IMAGE_FORMATS:
        raise ProductManagerError("Formats d'image acceptés : JPEG, PNG et WEBP.")
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    key = f"products/manager/{digest[:2]}/{digest}{IMAGE_FORMATS[image_format]}"
    return path, {"filename": path.name, "sha256": digest, "format": image_format, "key": key}


def _decimal(value, label="Prix"):
    try:
        amount = Decimal(str(value).strip())
    except (InvalidOperation, ValueError):
        raise ProductManagerError(f"{label} invalide.") from None
    if not amount.is_finite() or amount <= 0 or amount > MAX_PRODUCT_PRICE or amount.as_tuple().exponent < -2:
        raise ProductManagerError(f"{label} doit être positif avec deux décimales maximum.")
    if len(amount.as_tuple().digits) > 12:
        raise ProductManagerError(f"{label} dépasse la capacité du champ prix.")
    return amount.quantize(Decimal("0.01"))


def _stock(value):
    raw = str(value).strip()
    if not re.fullmatch(r"\d+", raw):
        raise ProductManagerError("Le stock doit être un entier positif ou nul.")
    amount = int(raw)
    if amount > 2_147_483_647:
        raise ProductManagerError("Le stock dépasse la valeur autorisée.")
    return amount


def _current_field(product, field_name):
    if field_name == "collection":
        return {"id": product.category_id, "name": product.category.name}
    if field_name == "image":
        return product.image.name if product.image else ""
    if field_name == "price":
        return format(product.price, ".2f")
    return getattr(product, field_name)


def _add_change(row, product, field_name, after):
    if field_name == "price":
        after = format(Decimal(str(after)).quantize(Decimal("0.01")), ".2f")
    before = _current_field(product, field_name)
    if before != after:
        row.changes[field_name] = {"before": before, "after": after}


def _parse_csv(csv_path):
    path = Path(csv_path)
    try:
        if path.stat().st_size > MAX_CSV_BYTES:
            raise ProductManagerError("CSV trop volumineux (maximum 25 Mo).")
        raw_bytes = path.read_bytes()
        text = raw_bytes.decode("utf-8-sig")
    except ProductManagerError:
        raise
    except (OSError, UnicodeDecodeError) as exc:
        raise ProductManagerError(f"CSV illisible ({type(exc).__name__}).") from None
    try:
        dialect = csv.Sniffer().sniff(text[:4096], delimiters=",;\t")
    except csv.Error:
        dialect = csv.excel
    reader = csv.DictReader(io.StringIO(text, newline=""), dialect=dialect)
    if not reader.fieldnames:
        raise ProductManagerError("Le CSV ne contient pas d'en-têtes.")
    headers = [_header(field) for field in reader.fieldnames]
    if "" in headers or len(headers) != len(set(headers)):
        raise ProductManagerError("Le CSV contient des en-têtes vides ou répétés.")
    if not ({"id", "sku"} & set(headers)):
        raise ProductManagerError("Le CSV doit contenir id ou sku pour identifier chaque produit.")
    raw_rows = []
    for raw in reader:
        row_number = reader.line_num
        if raw.get(None) is not None or any(value is None for value in raw.values()):
            raw_rows.append({"_row_number": row_number, "_error": "Nombre de cellules différent du nombre de colonnes."})
            continue
        if not any((value or "").strip() for value in raw.values()):
            continue
        raw_rows.append({"_row_number": row_number, **{headers[index]: value for index, value in enumerate(raw.values())}})
        if len(raw_rows) > MAX_ROWS:
            raise ProductManagerError("Limite de 5 000 lignes par fichier atteinte.")
    if not raw_rows:
        raise ProductManagerError("Le CSV ne contient aucune ligne produit.")
    return raw_bytes, raw_rows


def _row_targets(raw_rows):
    ids = set()
    skus = set()
    for raw in raw_rows:
        if raw.get("_error"):
            continue
        if raw.get("id", "").strip():
            try:
                ids.add(int(raw["id"].strip()))
            except ValueError:
                pass
        if raw.get("sku", "").strip():
            skus.add(raw["sku"].strip().upper())
    products = list(
        Product.objects.filter(Q(pk__in=ids) | Q(sku__in=skus)).select_related("category").order_by("pk")
    )
    by_id = {product.pk: product for product in products}
    by_sku = {product.sku.upper(): product for product in products if product.sku}
    return raw_rows, by_id, by_sku


def _parse_updates(raw, operation, images_dir):
    updates = {}
    errors = []
    for field_name, value in raw.items():
        if field_name.startswith("_") or field_name in ("id", "sku") or not value.strip():
            continue
        if field_name in ("is_active",):
            errors.append("La publication doit passer par l'action publish/unpublish, pas par le CSV de mise à jour.")
            continue
        if operation == "set-price" and field_name != "price":
            errors.append(f"Colonne {field_name} interdite pour set-price; seules les colonnes id/sku/price sont acceptées.")
            continue
        if operation == "set-stock" and field_name != "stock":
            errors.append(f"Colonne {field_name} interdite pour set-stock; seules les colonnes id/sku/stock sont acceptées.")
            continue
        if field_name not in EDITABLE_CSV_FIELDS:
            errors.append(f"Colonne non modifiable : {field_name}.")
            continue
        try:
            if field_name == "price":
                updates[field_name] = _decimal(value)
            elif field_name == "stock":
                updates[field_name] = _stock(value)
            elif field_name == "name":
                name = value.strip()
                if not name or len(name) > 160:
                    raise ProductManagerError("Nom obligatoire, 160 caractères maximum.")
                updates[field_name] = name
            elif field_name == "description":
                description = value.strip()
                if not description:
                    raise ProductManagerError("Description vide refusée.")
                updates[field_name] = description
            elif field_name == "collection":
                category = _category_lookup(value)
                updates[field_name] = {"id": category.pk, "name": category.name}
            elif field_name == "image_filename":
                if not value.strip():
                    continue
                path, asset = _resolve_image(images_dir, value)
                updates["image"] = asset["key"]
                updates["image_url"] = ""
                updates["_asset"] = {**asset, "source_path": str(path)}
        except ProductManagerError as exc:
            errors.append(f"{field_name}: {exc}")
    if operation == "set-price" and "price" not in updates:
        errors.append("Colonne price obligatoire pour set-price.")
    if operation == "set-stock" and "stock" not in updates:
        errors.append("Colonne stock obligatoire pour set-stock.")
    if operation == "update-csv" and not any(key in updates for key in EDITABLE_CSV_FIELDS | {"image", "image_url"}):
        errors.append("Aucune valeur modifiable fournie pour cette ligne.")
    return updates, errors


def _make_row(product, updates=None, errors=None):
    row = ChangeRow(
        product_id=product.pk,
        sku=product.sku or "",
        label=product.name,
        version=_product_version(product),
        errors=list(errors or []),
    )
    for field_name, value in (updates or {}).items():
        if field_name == "_asset":
            row.assets[field_name] = value
            continue
        _add_change(row, product, field_name, value)
    return row


def _read_csv_rows(csv_path, operation, images_dir):
    raw_bytes, raw_rows = _parse_csv(csv_path)
    _, by_id, by_sku = _row_targets(raw_rows)
    rows = []
    seen = set()
    for raw in raw_rows:
        if raw.get("_error"):
            rows.append(ChangeRow(0, "", f"CSV ligne {raw['_row_number']}", {}, errors=[raw["_error"]]))
            continue
        product = None
        errors = []
        raw_id = (raw.get("id") or "").strip()
        raw_sku = (raw.get("sku") or "").strip().upper()
        if raw_id:
            try:
                product = by_id.get(int(raw_id))
            except ValueError:
                errors.append("id invalide.")
        if raw_sku:
            sku_product = by_sku.get(raw_sku)
            if product and sku_product and product.pk != sku_product.pk:
                errors.append("id et SKU désignent deux produits différents.")
            elif not product:
                product = sku_product
        if not product and not errors:
            errors.append(f"Produit introuvable (id={raw_id or '—'}, SKU={raw_sku or '—'}).")
        if product and product.pk in seen:
            errors.append("Produit répété dans le CSV.")
        if product:
            seen.add(product.pk)
        updates, update_errors = _parse_updates(raw, operation, images_dir)
        errors.extend(update_errors)
        if product:
            row = _make_row(product, updates, errors)
        else:
            row = ChangeRow(0, raw_sku, f"CSV ligne {raw['_row_number']}", {}, errors=errors)
        rows.append(row)
    return rows, {"csv_sha256": hashlib.sha256(raw_bytes).hexdigest(), "source_filename": Path(csv_path).name}


def _selector_products(options):
    product_id = options.get("product_id")
    sku = (options.get("sku") or "").strip().upper()
    collection = (options.get("collection") or "").strip()
    provided = sum(bool(value) for value in (product_id, sku, collection))
    if provided != 1:
        raise ProductManagerError("Fournissez exactement un sélecteur : --product-id, --sku ou --collection.")
    if product_id:
        product = Product.objects.select_related("category").filter(pk=product_id).first()
        if not product:
            raise ProductManagerError(f"Produit ID {product_id} introuvable.")
        return [product]
    if sku:
        product = Product.objects.select_related("category").filter(sku=sku).first()
        if not product:
            raise ProductManagerError(f"SKU {sku} introuvable.")
        return [product]
    category = _category_lookup(collection)
    return list(Product.objects.filter(category=category).select_related("category").order_by("pk"))


def _fingerprint_payload(operation, params, rows, category_changes, source_meta):
    payload = {
        "version": 1,
        "operation": operation,
        "params": params,
        "source": source_meta,
        "rows": [
            {
                "product_id": row.product_id,
                "sku": row.sku,
                "version": row.version,
                "changes": row.changes,
                "errors": row.errors,
                "assets": [{key: value for key, value in asset.items() if key != "source_path"} for asset in row.assets.values()],
            }
            for row in rows
        ],
        "categories": [asdict(change) for change in category_changes],
    }
    return hashlib.sha256(_json(payload).encode("utf-8")).hexdigest()


def build_product_change_plan(action, options):
    """Build a read-only plan. Any validation failure blocks the entire apply."""
    rows = []
    category_changes = []
    params = {}
    source_meta = {}
    errors = []
    try:
        if action in ("set-price", "set-stock", "update-csv") and options.get("csv"):
            if any(options.get(key) for key in ("product_id", "sku", "collection")):
                raise ProductManagerError("Avec --csv, ne fournissez pas aussi --product-id, --sku ou --collection.")
        if action == "set-price" and options.get("percent") is not None and options.get("price") is not None:
            raise ProductManagerError("Choisissez soit --price, soit --percent.")
        if action == "set-price" and options.get("csv") and (options.get("price") is not None or options.get("percent") is not None):
            raise ProductManagerError("Avec un CSV de prix, les valeurs viennent du fichier; retirez --price/--percent.")
        if action == "set-stock" and options.get("csv") and options.get("stock") is not None:
            raise ProductManagerError("Avec un CSV de stock, les quantités viennent du fichier; retirez --stock.")
        if action in ("publish", "unpublish") and options.get("csv") and any(options.get(key) for key in ("product_id", "sku", "collection")):
            raise ProductManagerError("Avec un CSV de sélection, ne fournissez pas aussi un sélecteur.")
        if action == "update" and options.get("csv"):
            raise ProductManagerError("Pour les changements CSV, utilisez --action update-csv.")
        if action in ("set-price", "set-stock", "update-csv") and options.get("csv"):
            rows, source_meta = _read_csv_rows(options["csv"], action, options.get("images"))
            if action == "set-price" and "price" not in _csv_headers(options["csv"]):
                errors.append("Le CSV set-price doit contenir price.")
            if action == "set-stock" and "stock" not in _csv_headers(options["csv"]):
                errors.append("Le CSV set-stock doit contenir stock.")
        elif action == "prepare-collection":
            category = _category_lookup(options.get("collection", ""))
            params = {"collection": category.name}
            if category.is_active:
                category_changes.append(CategoryChange(
                    category_id=category.pk,
                    label=category.name,
                    version=_category_version(category),
                    before={"is_active": True},
                    after={"is_active": False},
                ))
            products = list(Product.objects.filter(category=category).select_related("category").order_by("pk"))
            rows = [_make_row(product, {"is_active": False}) for product in products]
        elif action in ("publish", "unpublish"):
            products, source_meta = _resolve_selection(options)
            desired = action == "publish"
            rows = [_make_row(product, {"is_active": desired}) for product in products]
            params = {"desired_publication": desired, "collection": options.get("collection") or ""}
        elif action in ("set-price", "set-stock", "update-csv"):
            products = _selector_products(options)
            if action == "set-price":
                if options.get("percent") is not None:
                    if not options.get("collection"):
                        raise ProductManagerError("Un pourcentage de prix doit cibler une --collection.")
                    percent = Decimal(str(options["percent"]))
                    if not percent.is_finite() or percent <= -100 or percent > 1000:
                        raise ProductManagerError("Le pourcentage doit être supérieur à -100 % et inférieur ou égal à 1000 %.")
                    params = {"collection": options.get("collection"), "percent": format(percent, "f")}
                    rows = []
                    for product in products:
                        new_price = (product.price * (Decimal("1") + percent / Decimal("100"))).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
                        if new_price <= 0 or new_price > MAX_PRODUCT_PRICE or len(new_price.as_tuple().digits) > 12:
                            raise ProductManagerError(f"Le pourcentage donnerait un prix invalide pour {product.name}.")
                        rows.append(_make_row(product, {"price": new_price}))
                else:
                    price = _decimal(options.get("price"), "Prix")
                    params = {"selector": options.get("sku") or options.get("product_id") or options.get("collection"), "price": format(price, ".2f")}
                    rows = [_make_row(product, {"price": price}) for product in products]
            elif action == "set-stock":
                stock = _stock(options.get("stock"))
                params = {"selector": options.get("sku") or options.get("product_id") or options.get("collection"), "stock": stock}
                rows = [_make_row(product, {"stock": stock}) for product in products]
            else:
                raise ProductManagerError("update-csv exige --csv.")
        elif action == "update":
            products = _selector_products(options)
            updates = {}
            if options.get("name") is not None:
                name = options["name"].strip()
                if not name or len(name) > 160:
                    raise ProductManagerError("Nom obligatoire, 160 caractères maximum.")
                updates["name"] = name
            if options.get("description") is not None:
                description = options["description"].strip()
                if not description:
                    raise ProductManagerError("Description vide refusée.")
                updates["description"] = description
            if options.get("target_collection"):
                category = _category_lookup(options["target_collection"])
                updates["collection"] = {"id": category.pk, "name": category.name}
            if options.get("image_filename"):
                path, asset = _resolve_image(options.get("images"), options["image_filename"])
                updates["image"] = asset["key"]
                updates["image_url"] = ""
                updates["_asset"] = {**asset, "source_path": str(path)}
            if not updates:
                raise ProductManagerError("Fournissez au moins --name, --description, --target-collection ou --image-filename.")
            params = {
                "selector": options.get("sku") or options.get("product_id"),
                "fields": sorted(key for key in updates if not key.startswith("_")),
            }
            rows = [_make_row(product, updates) for product in products]
        else:
            raise ProductManagerError(f"Action de modification inconnue : {action}.")
    except (ProductManagerError, InvalidOperation, ValueError) as exc:
        errors.append(str(exc) or "Paramètres invalides.")

    all_errors = list(errors)
    for row in rows:
        all_errors.extend(f"{row.label} : {message}" for message in row.errors)
    if action in ("set-price", "set-stock", "publish", "unpublish") and not rows and not errors:
        all_errors.append("Aucun produit ne correspond à la sélection.")
    if any(row.errors for row in rows) or errors:
        # Preview may show valid rows too, but APPLY remains all-or-nothing.
        pass
    params = {**params, "action": action}
    fingerprint = _fingerprint_payload(action, params, rows, category_changes, source_meta)
    return ProductChangePlan(action, params, rows, category_changes, all_errors, fingerprint)


def _csv_headers(csv_path):
    raw, _ = _parse_csv(csv_path)
    text = raw.decode("utf-8-sig")
    try:
        dialect = csv.Sniffer().sniff(text[:4096], delimiters=",;\t")
    except csv.Error:
        dialect = csv.excel
    return {_header(value) for value in csv.DictReader(io.StringIO(text, newline=""), dialect=dialect).fieldnames or []}


def _resolve_selection(options):
    if options.get("csv"):
        raw_bytes, raw_rows = _parse_csv(options["csv"])
        _, by_id, by_sku = _row_targets(raw_rows)
        selected = []
        seen = set()
        for raw in raw_rows:
            if raw.get("_error"):
                raise ProductManagerError(f"CSV ligne {raw['_row_number']} : {raw['_error']}")
            product = None
            if (raw.get("id") or "").strip():
                try:
                    product = by_id.get(int(raw["id"].strip()))
                except ValueError:
                    raise ProductManagerError(f"ID invalide à la ligne {raw['_row_number']}.") from None
            sku = (raw.get("sku") or "").strip().upper()
            if sku:
                by_sku_product = by_sku.get(sku)
                if product and by_sku_product and product.pk != by_sku_product.pk:
                    raise ProductManagerError(f"ID et SKU en conflit à la ligne {raw['_row_number']}.")
                product = product or by_sku_product
            if not product:
                raise ProductManagerError(f"Produit introuvable à la ligne {raw['_row_number']}.")
            if product.pk in seen:
                raise ProductManagerError(f"Produit répété à la ligne {raw['_row_number']}.")
            seen.add(product.pk)
            selected.append(product)
        return selected, {"csv_sha256": hashlib.sha256(raw_bytes).hexdigest(), "source_filename": Path(options['csv']).name}
    return _selector_products(options), {}


def build_update_csv_plan(csv_path, images_dir=None):
    """Public helper for direct CSV update planning."""
    return build_product_change_plan("update-csv", {"csv": csv_path, "images": images_dir})


def build_search_results(options):
    products = Product.objects.select_related("category").all()
    query = (options.get("query") or "").strip()
    if query:
        products = products.filter(Q(name__icontains=query) | Q(description__icontains=query) | Q(sku__icontains=query))
    if options.get("collection"):
        products = products.filter(category__name__iexact=options["collection"].strip())
    if options.get("missing_sku"):
        products = products.filter(Q(sku__isnull=True) | Q(sku=""))
    if options.get("out_of_stock"):
        products = products.filter(stock=0)
    if options.get("low_stock"):
        products = products.filter(stock__gt=0, stock__lte=options["low_stock"])
    visibility = options.get("visibility") or "all"
    if visibility == "published":
        products = products.filter(is_active=True)
    elif visibility == "draft":
        products = products.filter(is_active=False)
    result = []
    for product in products.order_by("pk")[:1000]:
        result.append({
            "id": product.pk,
            "sku": product.sku or "",
            "name": product.name,
            "collection": product.category.name,
            "price": format(product.price, ".2f"),
            "stock": product.stock,
            "is_active": product.is_active,
            "image": product.image.name if product.image else "",
        })
    return result


def format_search_results(results):
    lines = [f"Résultats : {len(results)} produit(s)" + (" (maximum affiché : 1 000)." if len(results) == 1000 else ".")]
    for item in results:
        lines.append(
            f"ID={item['id']} | SKU={item['sku'] or '—'} | {item['name']} | "
            f"collection={item['collection']} | prix={item['price']} | stock={item['stock']} | "
            f"{'publié' if item['is_active'] else 'brouillon'} | image={item['image'] or '—'}"
        )
    return "\n".join(lines)
def format_product_plan(plan):
    lines = [
        f"PREVIEW — {plan.operation}",
        f"Empreinte à approuver : {plan.fingerprint}",
        f"Produits concernés : {len(plan.rows)} | collections : {len(plan.category_changes)}",
    ]
    for row in plan.rows:
        lines.append(f"ID={row.product_id} | SKU={row.sku or '—'} | {row.label}")
        for name, change in row.changes.items():
            lines.append(f"  {name}: {change['before']} -> {change['after']}")
        for asset in row.assets.values():
            lines.append(f"  image source={asset['filename']} | destination={asset['key']}")
        for error in row.errors:
            lines.append(f"  ERREUR: {error}")
        if not row.changes:
            lines.append("  aucun changement")
    for change in plan.category_changes:
        lines.append(f"Collection {change.label} : {change.before} -> {change.after}")
    for error in plan.errors:
        lines.append(f"CONFLIT: {error}")
    if not plan.has_changes:
        lines.append("Aucun changement à appliquer.")
    lines.append("PREVIEW n'écrit aucune donnée en base et n'envoie aucune image.")
    return "\n".join(lines)


def _store_plan_assets(plan):
    stored = {}
    for row in plan.rows:
        asset = row.assets.get("_asset")
        if not asset:
            continue
        key = asset["key"]
        if key in stored:
            continue
        path = Path(asset["source_path"])
        if not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest() != asset["sha256"]:
            raise ProductManagerError(f"Image modifiée depuis PREVIEW : {asset['filename']}.")
        if default_storage.exists(key):
            with default_storage.open(key, "rb") as saved:
                if hashlib.sha256(saved.read()).hexdigest() != asset["sha256"]:
                    raise ProductManagerError("Collision de stockage détectée; aucun fichier n'a été écrasé.")
        else:
            saved_name = default_storage.save(key, ContentFile(path.read_bytes()))
            if saved_name != key:
                raise ProductManagerError("Le chemin de stockage existe déjà; aucune image n'a été écrasée.")
        stored[key] = key
    return stored


def _current_product_version(product):
    return _product_version(product)


def apply_product_change_plan(plan):
    if plan.errors:
        raise ProductManagerError("L'aperçu contient des erreurs/conflits; aucune modification n'a été appliquée.")
    if not plan.has_changes:
        return {"status": "no_changes", "fingerprint": plan.fingerprint, "changed": 0, "already_applied": False}

    # Storage is not part of the SQL transaction. Content-addressed names make retries safe;
    # an object uploaded before a later DB conflict may remain unused, but is never overwritten/deleted.
    _store_plan_assets(plan)
    try:
        with transaction.atomic():
            existing_batch = ProductChangeBatch.objects.select_for_update().filter(fingerprint=plan.fingerprint).first()
            if existing_batch:
                return {
                    "status": existing_batch.status,
                    "fingerprint": existing_batch.fingerprint,
                    "changed": existing_batch.changes.count(),
                    "already_applied": True,
                }

            target_category_names = {
                change["after"]["id"]: change["after"]["name"]
                for row in plan.rows
                for change in row.changes.values()
                if isinstance(change.get("after"), dict) and "id" in change["after"] and "name" in change["after"]
            }
            category_ids = sorted({change.category_id for change in plan.category_changes} | set(target_category_names))
            locked_categories = {
                category.pk: category
                for category in Category.objects.select_for_update().filter(pk__in=category_ids).order_by("pk")
            }
            product_ids = sorted(row.product_id for row in plan.rows if row.changes)
            locked_products = {
                product.pk: product
                for product in Product.objects.select_for_update().select_related("category").filter(pk__in=product_ids).order_by("pk")
            }
            for row in plan.rows:
                if not row.changes:
                    continue
                product = locked_products.get(row.product_id)
                if not product or _current_product_version(product) != row.version:
                    raise StaleProductPlan(f"Le produit ID {row.product_id} a changé depuis PREVIEW.")
            for change in plan.category_changes:
                category = locked_categories.get(change.category_id)
                if not category or _category_version(category) != change.version:
                    raise StaleProductPlan(f"La collection {change.label} a changé depuis PREVIEW.")

            for category_id, expected_name in target_category_names.items():
                category = locked_categories.get(category_id)
                if not category or category.name != expected_name:
                    raise StaleProductPlan("La collection cible a changé depuis PREVIEW.")

            batch = ProductChangeBatch.objects.create(
                fingerprint=plan.fingerprint,
                operation=plan.operation,
                details={"params": plan.params, "products": len(product_ids), "categories": len(category_ids)},
            )
            for row in plan.rows:
                if not row.changes:
                    continue
                product = locked_products[row.product_id]
                before = {field_name: change["before"] for field_name, change in row.changes.items()}
                after = {field_name: change["after"] for field_name, change in row.changes.items()}
                update_fields = []
                for field_name, value in after.items():
                    if field_name == "collection":
                        product.category_id = value["id"]
                        update_fields.append("category")
                    elif field_name == "image":
                        product.image.name = value
                        update_fields.append("image")
                    elif field_name == "price":
                        product.price = Decimal(value)
                        update_fields.append("price")
                    else:
                        setattr(product, field_name, value)
                        update_fields.append(field_name)
                product.save(update_fields=tuple(update_fields + ["updated_at"]))
                ProductChangeLog.objects.create(
                    batch=batch,
                    target_type=ProductChangeLog.TargetType.PRODUCT,
                    target_id_snapshot=product.pk,
                    label_snapshot=product.name,
                    sku_snapshot=row.sku,
                    product=product,
                    before=before,
                    after=after,
                )
            for change in plan.category_changes:
                category = locked_categories[change.category_id]
                category.is_active = change.after["is_active"]
                category.save(update_fields=("is_active",))
                ProductChangeLog.objects.create(
                    batch=batch,
                    target_type=ProductChangeLog.TargetType.CATEGORY,
                    target_id_snapshot=category.pk,
                    label_snapshot=category.name,
                    category=category,
                    before=change.before,
                    after=change.after,
                )
        return {
            "status": "applied",
            "fingerprint": plan.fingerprint,
            "changed": len(product_ids) + len(category_ids),
            "already_applied": False,
        }
    except IntegrityError:
        # A concurrent retry may have committed the same approved fingerprint first.
        existing_batch = ProductChangeBatch.objects.filter(fingerprint=plan.fingerprint).first()
        if existing_batch:
            return {
                "status": existing_batch.status,
                "fingerprint": existing_batch.fingerprint,
                "changed": existing_batch.changes.count(),
                "already_applied": True,
            }
        raise


def format_apply_result(result):
    if result["already_applied"]:
        status = "déjà appliqué (reprise sans doublon)"
    elif result["status"] == "no_changes":
        status = "aucun changement"
    else:
        status = "appliqué"
    return f"{status} | empreinte={result['fingerprint']} | cibles modifiées={result['changed']}"


def get_change_history(fingerprint):
    batch = ProductChangeBatch.objects.filter(fingerprint=fingerprint.strip().lower()).first()
    if not batch:
        raise ProductManagerError("Journal introuvable pour cette empreinte.")
    result = []
    for change in batch.changes.all():
        result.append({
            "type": change.target_type,
            "id": change.target_id_snapshot,
            "label": change.label_snapshot,
            "sku": change.sku_snapshot,
            "before": change.before,
            "after": change.after,
        })
    return batch, result


def format_change_history(batch, changes):
    lines = [f"{batch.operation} | {batch.status} | {batch.created_at.isoformat()} | {batch.fingerprint}"]
    for change in changes:
        lines.append(
            f"{change['type']} ID={change['id']} SKU={change['sku'] or '—'} {change['label']} | "
            f"avant={_json(change['before'])} | après={_json(change['after'])}"
        )
    return "\n".join(lines)
