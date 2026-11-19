"""Safe, resumable CSV catalog imports."""

import csv
import hashlib
import io
import re
import warnings
from dataclasses import asdict, dataclass, field
from decimal import Decimal, InvalidOperation
from pathlib import Path

from django.core.files.base import ContentFile
from django.core.files.storage import default_storage
from django.db import IntegrityError, transaction
from django.utils.text import slugify
from PIL import Image, UnidentifiedImageError

from boutique.models import CatalogImportBatch, CatalogImportRow, Category, Product


MAX_IMAGE_BYTES = 20 * 1024 * 1024
MAX_TOTAL_IMAGE_BYTES = 2 * 1024 * 1024 * 1024
MAX_CSV_BYTES = 25 * 1024 * 1024
MAX_ROWS = 5000
ALLOWED_IMAGE_FORMATS = {"JPEG": ".jpg", "PNG": ".png", "WEBP": ".webp"}
REQUIRED_HEADERS = {"name", "description", "price", "stock", "image_filename", "is_active"}
HEADER_ALIASES = {
    "nom": "name",
    "nom_du_produit": "name",
    "nom_de_produit": "name",
    "sku": "sku",
    "sku_si_disponible": "sku",
    "description": "description",
    "prix": "price",
    "prix_fcfa": "price",
    "prix_en_fcfa": "price",
    "price": "price",
    "catégorie": "category",
    "categorie": "category",
    "category": "category",
    "collection": "collection",
    "stock": "stock",
    "image": "image_filename",
    "nom_du_fichier_image": "image_filename",
    "image_filename": "image_filename",
    "statut_de_publication": "is_active",
    "statut_de_publication_souhaite": "is_active",
    "statut_de_publication_souhaité": "is_active",
    "publication": "is_active",
    "is_active": "is_active",
}
TRUE_VALUES = {"1", "true", "yes", "oui", "published", "publish", "publie", "publié"}
FALSE_VALUES = {"0", "false", "no", "non", "draft", "unpublished", "brouillon", ""}


@dataclass
class ImportRowPlan:
    row_number: int
    name: str = ""
    sku: str = ""
    description: str = ""
    price: str = ""
    category_name: str = ""
    stock: int | None = None
    image_filename: str = ""
    image_sha256: str = ""
    image_format: str = ""
    image_size_bytes: int = 0
    publication_requested: bool = False
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    action: str = "create"

    def public_dict(self):
        result = asdict(self)
        result.pop("image_sha256")
        result.pop("image_format")
        result.pop("image_size_bytes")
        return result


@dataclass
class ImportPlan:
    source_filename: str
    fingerprint: str
    rows: list[ImportRowPlan]
    categories_to_create: list[str]
    unmatched_images: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    @property
    def can_apply(self):
        return not self.errors and bool(self.rows)


class CatalogImportError(Exception):
    pass


def _normalise_header(value):
    raw = (value or "").strip().lower().replace(" ", "_").replace("-", "_")
    return HEADER_ALIASES.get(raw, raw)


def _safe_image_path(image_directory: Path, filename: str) -> Path:
    candidate = Path(filename)
    if not filename or candidate.name != filename or candidate.is_absolute() or "/" in filename or "\\" in filename:
        raise CatalogImportError("Le nom d'image doit désigner un fichier directement dans le dossier fourni.")
    resolved_dir = image_directory.resolve(strict=True)
    resolved_file = (resolved_dir / candidate).resolve(strict=True)
    if resolved_file.parent != resolved_dir or not resolved_file.is_file():
        raise CatalogImportError("Fichier image introuvable ou hors du dossier d'images.")
    return resolved_file


def _inspect_image(path: Path):
    if path.stat().st_size > MAX_IMAGE_BYTES:
        raise CatalogImportError(f"Image trop volumineuse (maximum {MAX_IMAGE_BYTES // (1024 * 1024)} Mo).")
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(path) as image:
                image_format = image.format
                image.verify()
    except (UnidentifiedImageError, OSError, Image.DecompressionBombError, Image.DecompressionBombWarning):
        raise CatalogImportError("Fichier image invalide ou non pris en charge.") from None
    if image_format not in ALLOWED_IMAGE_FORMATS:
        raise CatalogImportError("Formats acceptés : JPEG, PNG et WEBP.")
    return image_format, hashlib.sha256(path.read_bytes()).hexdigest()


def _parse_bool(value):
    normalized = (value or "").strip().lower()
    if normalized in TRUE_VALUES:
        return True
    if normalized in FALSE_VALUES:
        return False
    raise CatalogImportError("Statut de publication invalide; utiliser true/false ou oui/non.")


def _auto_image_filename(name, sku, image_directory):
	"""Match an exact SKU or slug basename; never use fuzzy matching."""
	image_directory = Path(image_directory)
	keys = {key.casefold() for key in (sku, slugify(name)) if key}
	if not keys:
		return ""
	allowed_extensions = {".jpg", ".jpeg", ".png", ".webp"}
	matches = sorted(
		path.name for path in image_directory.iterdir()
		if path.is_file() and path.suffix.casefold() in allowed_extensions and path.stem.casefold() in keys
	)
	if len(matches) > 1:
		raise CatalogImportError("Plusieurs images correspondent exactement au SKU/nom; indiquez image_filename.")
	return matches[0] if matches else ""


def _parse_row(row_number, raw, image_directory, image_cache):
    row = ImportRowPlan(row_number=row_number)
    try:
        row.name = (raw.get("name") or "").strip()
        row.sku = (raw.get("sku") or "").strip().upper()
        row.description = (raw.get("description") or "").strip()
        row.category_name = (raw.get("collection") or raw.get("category") or "").strip()
        row.image_filename = (raw.get("image_filename") or "").strip()
        if not row.image_filename and image_directory.is_dir():
            try:
                row.image_filename = _auto_image_filename(row.name, row.sku, image_directory)
            except CatalogImportError as exc:
                row.errors.append(str(exc))
        if raw.get("category") and raw.get("collection"):
            if raw["category"].strip().casefold() != raw["collection"].strip().casefold():
                row.errors.append("category et collection diffèrent; Category est l'unique relation de collection.")
        if not row.name:
            row.errors.append("Nom du produit obligatoire.")
        elif len(row.name) > 160:
            row.errors.append("Nom trop long (160 caractères maximum).")
        if not row.description:
            row.errors.append("Description obligatoire.")
        if len(row.sku) > 80:
            row.errors.append("SKU trop long (80 caractères maximum).")
        if not row.category_name:
            row.errors.append("Collection/catégorie obligatoire.")
        elif len(row.category_name) > 80:
            row.errors.append("Nom de collection trop long (80 caractères maximum).")
        try:
            price = Decimal((raw.get("price") or "").strip())
            if not price.is_finite() or price <= 0 or price.as_tuple().exponent < -2 or len(price.as_tuple().digits) > 12:
                raise CatalogImportError("Prix invalide; saisir un montant positif avec deux décimales maximum.")
            row.price = format(price.quantize(Decimal("0.01")), "f")
        except (InvalidOperation, ValueError):
            row.errors.append("Prix invalide.")
        except CatalogImportError as exc:
            row.errors.append(str(exc))
        try:
            stock_text = (raw.get("stock") or "").strip()
            if not re.fullmatch(r"\d+", stock_text):
                raise ValueError
            row.stock = int(stock_text)
            if row.stock > 2_147_483_647:
                row.errors.append("Stock trop élevé.")
        except ValueError:
            row.errors.append("Stock invalide; saisir un entier positif ou nul.")
        try:
            row.publication_requested = _parse_bool(raw.get("is_active"))
        except CatalogImportError as exc:
            row.errors.append(str(exc))
        if row.publication_requested:
            row.warnings.append("Publication demandée, mais le produit sera créé en brouillon; publier séparément après approbation.")
        if row.image_filename:
            try:
                image_path = _safe_image_path(image_directory, row.image_filename)
                cache_key = str(image_path)
                if cache_key not in image_cache:
                    image_cache[cache_key] = (*_inspect_image(image_path), image_path.stat().st_size)
                row.image_format, row.image_sha256, row.image_size_bytes = image_cache[cache_key]
            except (CatalogImportError, FileNotFoundError, OSError) as exc:
                row.errors.append(str(exc))
        else:
            row.warnings.append("Aucune image principale fournie.")
    except Exception as exc:
        row.errors.append(f"Ligne invalide ({type(exc).__name__}).")
    if row.errors:
        row.action = "error"
    return row


def build_import_plan(csv_path, image_directory):
    csv_path = Path(csv_path)
    image_directory = Path(image_directory)
    try:
        if csv_path.stat().st_size > MAX_CSV_BYTES:
            raise CatalogImportError("CSV trop volumineux (maximum 25 Mo).")
        csv_bytes = csv_path.read_bytes()
        csv_text = csv_bytes.decode("utf-8-sig")
    except CatalogImportError:
        raise
    except (OSError, UnicodeDecodeError) as exc:
        raise CatalogImportError(f"Fichier CSV illisible ({type(exc).__name__}).") from None
    if not image_directory.is_dir():
        raise CatalogImportError("Le dossier d'images fourni est introuvable.")
    try:
        dialect = csv.Sniffer().sniff(csv_text[:4096], delimiters=",;\t")
    except csv.Error:
        dialect = csv.excel
    reader = csv.DictReader(io.StringIO(csv_text, newline=""), dialect=dialect)
    if not reader.fieldnames:
        raise CatalogImportError("Le CSV ne contient pas d'en-têtes.")
    headers = [_normalise_header(header) for header in reader.fieldnames]
    if "" in headers:
        raise CatalogImportError("Le CSV contient un en-tête vide.")
    if len(headers) != len(set(headers)):
        raise CatalogImportError("Le CSV contient des en-têtes en double après normalisation.")
    reader.fieldnames = headers
    missing_headers = REQUIRED_HEADERS - set(headers)
    if not ({"category", "collection"} & set(headers)):
        missing_headers.add("collection (ou category)")
    if missing_headers:
        raise CatalogImportError("Colonnes obligatoires absentes : " + ", ".join(sorted(missing_headers)))

    rows = []
    image_cache = {}
    for raw in reader:
        row_number = reader.line_num
        if raw.get(None) is not None or any(value is None for value in raw.values()):
            rows.append(ImportRowPlan(row_number=row_number, action="error", errors=["Nombre de cellules différent du nombre de colonnes."]))
            continue
        if not any((value or "").strip() for value in raw.values()):
            continue
        planned_row = _parse_row(row_number, raw, image_directory, image_cache)
        rows.append(planned_row)
        unique_assets = {(image_hash, size) for _, image_hash, size in image_cache.values()}
        total_image_bytes = sum(size for _, size in unique_assets)
        if total_image_bytes > MAX_TOTAL_IMAGE_BYTES:
            raise CatalogImportError("Le volume total des images dépasse la limite de 2 Go par import.")
        if len(rows) > MAX_ROWS:
            raise CatalogImportError("Le CSV dépasse la limite de 5 000 produits par import.")
    if not rows:
        raise CatalogImportError("Le CSV ne contient aucune ligne produit.")

    sku_rows = {}
    name_rows = {}
    for row in rows:
        if row.sku:
            sku_rows.setdefault(row.sku, []).append(row)
        else:
            name_rows.setdefault((row.category_name.casefold(), row.name.casefold()), []).append(row)
    for duplicate_rows in sku_rows.values():
        if len(duplicate_rows) > 1:
            for row in duplicate_rows:
                row.errors.append("SKU répété dans le même fichier.")
                row.action = "error"
    for duplicate_rows in name_rows.values():
        if len(duplicate_rows) > 1:
            for row in duplicate_rows:
                row.errors.append("Nom et collection répétés sans SKU; fournissez un SKU pour lever l'ambiguïté.")
                row.action = "error"

    categories_present = {
        name.casefold()
        for name in Category.objects.values_list("name", flat=True)
    }
    missing_categories = {}
    for row in rows:
        if row.category_name and not row.errors and row.category_name.casefold() not in categories_present:
            missing_categories.setdefault(row.category_name.casefold(), row.category_name)
    categories_to_create = sorted(missing_categories.values(), key=str.casefold)

    digest = hashlib.sha256()
    digest.update(b"yorwani-catalog-import-v1\0")
    digest.update(csv_bytes)
    existing_product_rows = Product.objects.values_list("sku", "name", "category__name")
    existing_skus = set()
    existing_names = {}
    for existing_sku, existing_name, existing_category in existing_product_rows.iterator(chunk_size=2000):
        if existing_sku:
            existing_skus.add(existing_sku.upper())
        existing_names.setdefault((existing_category.casefold(), existing_name.casefold()), 0)
        existing_names[(existing_category.casefold(), existing_name.casefold())] += 1

    for row in rows:
        digest.update(f"\0{row.row_number}:{row.image_filename}:{row.image_sha256}".encode("utf-8"))
        if row.errors:
            continue
        if row.sku and row.sku in existing_skus:
            row.action = "skip-existing-sku"
            row.warnings.append("SKU déjà présent; la ligne existante ne sera pas modifiée.")
        else:
            exact_matches = existing_names.get((row.category_name.casefold(), row.name.casefold()), 0)
            if not row.sku and exact_matches:
                row.action = "error"
                row.errors.append("Produit de même nom déjà présent; ajoutez un SKU pour garantir l'identité.")
            elif row.sku:
                row.action = "create-with-sku"
            else:
                row.action = "create-without-sku"

    referenced_images = {row.image_filename for row in rows if row.image_filename and not row.errors}
    supported_extensions = {".jpg", ".jpeg", ".png", ".webp"}
    unmatched_images = sorted(
        path.name for path in image_directory.iterdir()
        if path.is_file() and path.suffix.casefold() in supported_extensions and path.name not in referenced_images
    )
    return ImportPlan(
        source_filename=csv_path.name[:255],
        fingerprint=digest.hexdigest(),
        rows=rows,
        categories_to_create=categories_to_create,
        unmatched_images=unmatched_images,
    )


def format_preview(plan):
    lines = [
        f"Aperçu catalogue — {plan.source_filename}",
        f"Empreinte d'approbation : {plan.fingerprint}",
        f"Collections à créer : {', '.join(plan.categories_to_create) or 'aucune'}",
        f"Images non associées : {', '.join(plan.unmatched_images) or 'aucune'}",
    ]
    for row in plan.rows:
        image = row.image_filename or "(aucune)"
        lines.append(
            f"L{row.row_number} | {row.action} | {row.name or '(sans nom)'} | "
            f"SKU={row.sku or '—'} | collection={row.category_name or '—'} | "
            f"prix={row.price or '—'} | stock={row.stock if row.stock is not None else '—'} | "
            f"image={image} | publié demandé={row.publication_requested}"
        )
        for error in row.errors:
            lines.append(f"  ERREUR: {error}")
        for warning in row.warnings:
            lines.append(f"  INFO: {warning}")
    lines.append(
        f"Total: {len(plan.rows)} lignes; {sum(not row.errors for row in plan.rows)} valides; "
        f"{sum(bool(row.errors) for row in plan.rows)} en erreur."
    )
    lines.append("PREVIEW n'écrit rien en base et n'envoie aucun fichier.")
    return "\n".join(lines)


def _storage_key_for(row):
    extension = ALLOWED_IMAGE_FORMATS[row.image_format]
    return f"products/imports/{row.image_sha256[:2]}/{row.image_sha256}{extension}"


def _store_image(row, image_directory, stored_images):
    if not row.image_filename:
        return ""
    source_path = _safe_image_path(Path(image_directory), row.image_filename)
    image_format, actual_hash = _inspect_image(source_path)
    if actual_hash != row.image_sha256 or image_format != row.image_format:
        raise CatalogImportError("Le fichier image a changé depuis l'aperçu; relancez PREVIEW.")
    target_name = _storage_key_for(row)
    if target_name in stored_images:
        return stored_images[target_name]
    if default_storage.exists(target_name):
        with default_storage.open(target_name, "rb") as existing:
            existing_hash = hashlib.sha256(existing.read()).hexdigest()
        if existing_hash != actual_hash:
            raise CatalogImportError("Collision de stockage détectée; aucun fichier n'a été écrasé.")
        stored_images[target_name] = target_name
        return target_name
    saved_name = default_storage.save(target_name, ContentFile(source_path.read_bytes()))
    if saved_name != target_name:
        raise CatalogImportError("Un objet de même nom existe; l'image n'a pas été associée.")
    stored_images[target_name] = saved_name
    return saved_name


def _get_or_create_category(name):
    matches = list(Category.objects.filter(name__iexact=name)[:2])
    if len(matches) > 1:
        raise CatalogImportError("Plusieurs collections ont le même nom sans tenir compte de la casse.")
    if matches:
        return matches[0], False
    try:
        with transaction.atomic():
            return Category.objects.create(name=name, is_active=False), True
    except IntegrityError:
        category = Category.objects.filter(name__iexact=name).first()
        if category:
            return category, False
        raise


def _mark_row(row_record, status, message, product=None):
    row_record.status = status
    row_record.message = message[:4000]
    if product is not None:
        row_record.product = product
    row_record.save(update_fields=("status", "message", "product", "updated_at"))


def apply_import_plan(plan, image_directory):
    batch, _ = CatalogImportBatch.objects.get_or_create(
        fingerprint=plan.fingerprint,
        defaults={"source_filename": plan.source_filename, "status": CatalogImportBatch.Status.APPLYING},
    )
    report = {
        "created": 0,
        "skipped": 0,
        "errors": 0,
        "resumed": 0,
        "categories_created": [],
        "rows": [],
    }
    stored_images = {}
    for planned in plan.rows:
        row_record, _ = CatalogImportRow.objects.get_or_create(
            batch=batch,
            row_number=planned.row_number,
            defaults={
                "sku": planned.sku,
                "product_name": planned.name,
                "publication_requested": planned.publication_requested,
            },
        )
        if row_record.status in (CatalogImportRow.Status.CREATED, CatalogImportRow.Status.SKIPPED):
            report["resumed"] += 1
            if (
                row_record.status == CatalogImportRow.Status.CREATED
                and row_record.category_created
                and row_record.product_id
            ):
                category_name = row_record.product.category.name
                if category_name not in report["categories_created"]:
                    report["categories_created"].append(category_name)
            report["rows"].append({"row": planned.row_number, "status": row_record.status, "message": row_record.message})
            continue
        if planned.errors:
            _mark_row(row_record, CatalogImportRow.Status.ERROR, "; ".join(planned.errors))
            report["errors"] += 1
            report["rows"].append({"row": planned.row_number, "status": "error", "message": row_record.message})
            continue

        _mark_row(row_record, CatalogImportRow.Status.RUNNING, "Traitement en cours.")
        created_category_name = None
        try:
            with transaction.atomic():
                current_row = CatalogImportRow.objects.select_for_update().get(pk=row_record.pk)
                if current_row.status in (CatalogImportRow.Status.CREATED, CatalogImportRow.Status.SKIPPED):
                    report["resumed"] += 1
                    report["rows"].append({"row": planned.row_number, "status": current_row.status, "message": current_row.message})
                    continue
                if planned.sku:
                    existing = Product.objects.filter(sku=planned.sku).first()
                    if existing:
                        _mark_row(current_row, CatalogImportRow.Status.SKIPPED, "SKU existant; produit préservé sans mise à jour.", existing)
                        report["skipped"] += 1
                        report["rows"].append({"row": planned.row_number, "status": "skipped", "product": existing.pk})
                        continue
                category, category_created = _get_or_create_category(planned.category_name)
                if category_created:
                    created_category_name = category.name
                if not planned.sku:
                    matches = list(Product.objects.filter(category=category, name__iexact=planned.name)[:2])
                    if matches:
                        raise CatalogImportError("Produit de même nom déjà présent; ajoutez un SKU pour garantir l'identité.")
                image_key = _store_image(planned, image_directory, stored_images)
                product = Product.objects.create(
                    category=category,
                    sku=planned.sku or None,
                    name=planned.name,
                    description=planned.description,
                    price=Decimal(planned.price),
                    stock=planned.stock,
                    image=image_key,
                    is_active=False,
                )
                current_row.status = CatalogImportRow.Status.CREATED
                current_row.product = product
                current_row.category_created = category_created
                current_row.message = "Produit créé en brouillon." + (
                    " Publication demandée dans le fichier, à traiter séparément." if planned.publication_requested else ""
                )
                current_row.save(update_fields=("status", "product", "category_created", "message", "updated_at"))
            report["created"] += 1
            if created_category_name and created_category_name not in report["categories_created"]:
                report["categories_created"].append(created_category_name)
            report["rows"].append({"row": planned.row_number, "status": "created", "product": product.pk})
        except Exception as exc:
            _mark_row(row_record, CatalogImportRow.Status.ERROR, f"Échec ({type(exc).__name__}); la ligne pourra être reprise.")
            report["errors"] += 1
            report["rows"].append({"row": planned.row_number, "status": "error", "message": row_record.message})

    batch.refresh_from_db()
    has_errors = CatalogImportRow.objects.filter(batch=batch, status=CatalogImportRow.Status.ERROR).exists()
    has_unfinished = CatalogImportRow.objects.filter(
        batch=batch,
        status__in=(CatalogImportRow.Status.PENDING, CatalogImportRow.Status.RUNNING),
    ).exists()
    batch.status = CatalogImportBatch.Status.PARTIAL if has_errors or has_unfinished else CatalogImportBatch.Status.COMPLETED
    batch.save(update_fields=("status", "updated_at"))
    report["batch_status"] = batch.status
    report["fingerprint"] = batch.fingerprint
    return report


def format_apply_report(report):
    lines = [
        f"Import {report['fingerprint']} — {report['batch_status']}",
        f"Créés: {report['created']} | ignorés: {report['skipped']} | erreurs: {report['errors']} | déjà traités: {report['resumed']}",
        f"Collections créées: {', '.join(report['categories_created']) or 'aucune'}",
    ]
    for row in report["rows"]:
        lines.append(f"L{row['row']}: {row['status']}" + (f" — {row['message']}" if row.get("message") else ""))
    return "\n".join(lines)
