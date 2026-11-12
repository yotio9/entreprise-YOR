import hashlib
import io
import json
import os
import sqlite3
import uuid
from datetime import datetime, timezone as datetime_timezone
from pathlib import Path

from django.apps import apps
from django.conf import settings
from django.contrib.auth.models import Group
from django.core.management import call_command
from django.core.management.base import BaseCommand, CommandError
from django.db import connections, transaction
from django.utils import timezone


def json_value(value):
    # SQLite returns Django DateTimeField values as naive datetimes while
    # PostgreSQL returns aware values. Normalize both to UTC before comparing.
    if isinstance(value, datetime):
        if timezone.is_naive(value):
            value = timezone.make_aware(value, timezone.get_default_timezone())
        # Django's JSON fixture format keeps datetime precision to milliseconds.
        value = value.replace(microsecond=(value.microsecond // 1000) * 1000)
        return value.astimezone(datetime_timezone.utc).isoformat()
    return str(value)

def model_fingerprint(model, alias):
    digest = hashlib.sha256()
    field_names = [field.attname for field in model._meta.concrete_fields]
    rows = model.objects.using(alias).order_by("pk").values_list(*field_names)
    for row in rows.iterator():
        digest.update(json.dumps(row, default=json_value, sort_keys=True, separators=(",", ":")).encode("utf-8"))
        digest.update(b"\n")
    for relation in model._meta.many_to_many:
        for instance in model.objects.using(alias).only("pk").order_by("pk").iterator():
            related = list(
                getattr(instance, relation.name).using(alias)
                .order_by("pk").values_list("pk", flat=True)
            )
            digest.update(json.dumps(
                [relation.name, instance.pk, related],
                default=str, sort_keys=True, separators=(",", ":"),
            ).encode("utf-8"))
            digest.update(b"\n")
    return digest.hexdigest()


class Command(BaseCommand):
    help = "Copie les donnees SQLite vers PostgreSQL sans ecraser une cible contenant des donnees."

    def add_arguments(self, parser):
        parser.add_argument(
            "--confirm-target-empty",
            action="store_true",
            help="Confirme que la base PostgreSQL cible est dediee a cette migration.",
        )

    def handle(self, *args, **options):
        if not options["confirm_target_empty"]:
            raise CommandError("Ajoute --confirm-target-empty apres avoir verifie le projet PostgreSQL.")
        if connections["default"].vendor != "postgresql":
            raise CommandError("Configure DATABASE_URL sur une base PostgreSQL cible.")
        sqlite_path = Path(
            os.environ.get("SQLITE_DATABASE_PATH", settings.BASE_DIR / "db.sqlite3")
        )
        if not sqlite_path.is_absolute():
            sqlite_path = settings.BASE_DIR / sqlite_path
        if not sqlite_path.is_file():
            raise CommandError("La base SQLite source est introuvable.")
        if not apps.get_app_config("boutique").get_models():
            raise CommandError("Aucun modele boutique disponible.")

        business_models = list(apps.get_app_config("boutique").get_models()) + [Group]
        occupied = [
            model._meta.label
            for model in business_models
            if model.objects.using("default").exists()
        ]
        if occupied:
            raise CommandError(
                "La cible contient deja des donnees applicatives ; aucune copie effectuee. "
                "Utilise une base Neon vide."
            )

        source_alias = "migration_sqlite_source"
        # Start with Django's normalized database settings (which include
        # required defaults such as TIME_ZONE and AUTOCOMMIT), then switch the
        # backend. Adding a bare dict to connections.databases skips those
        # defaults and fails on current Django versions.
        source_database = connections.databases["default"].copy()
        source_database.update({
            "ENGINE": "django.db.backends.sqlite3",
            "NAME": str(sqlite_path),
            "OPTIONS": {"timeout": 30},
        })
        connections.databases[source_alias] = source_database
        backup_dir = settings.BASE_DIR / ".private_backups"
        backup_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        backup_path = backup_dir / f"sqlite-before-postgres-{stamp}.sqlite3"
        fixture_path = backup_dir / f"postgres-transfer-{uuid.uuid4().hex}.json"
        try:
            with sqlite3.connect(str(sqlite_path)) as source, sqlite3.connect(str(backup_path)) as backup:
                source.backup(backup)
            try:
                os.chmod(backup_path, 0o600)
            except OSError:
                pass

            source_counts = {
                model._meta.label: model.objects.using(source_alias).count()
                for model in business_models
            }
            source_fingerprints = {
                model._meta.label: model_fingerprint(model, source_alias)
                for model in business_models
            }
            serialized_data = io.StringIO()
            call_command(
                "dumpdata",
                "boutique",
                "auth.group",
                database=source_alias,
                natural_foreign=True,
                stdout=serialized_data,
                verbosity=0,
            )
            fixture_path.write_text(serialized_data.getvalue(), encoding="utf-8")
            try:
                os.chmod(fixture_path, 0o600)
            except OSError:
                pass

            with transaction.atomic(using="default"):
                call_command(
                    "loaddata",
                    str(fixture_path),
                    database="default",
                    verbosity=0,
                )
                mismatches = []
                for model in business_models:
                    expected = source_counts[model._meta.label]
                    actual = model.objects.using("default").count()
                    if expected != actual:
                        mismatches.append(f"{model._meta.label}: {expected} != {actual}")
                    elif source_fingerprints[model._meta.label] != model_fingerprint(model, "default"):
                        mismatches.append(f"{model._meta.label}: empreinte differente")
                if mismatches:
                    raise CommandError("Verification des nombres de lignes echouee : " + "; ".join(mismatches))
        except CommandError:
            raise
        except Exception as exc:
            raise CommandError(
                f"Migration interrompue ({type(exc).__name__}). La source n'a pas ete modifiee."
            ) from None
        finally:
            if fixture_path.exists():
                fixture_path.unlink()
            connections[source_alias].close()
            connections.databases.pop(source_alias, None)

        total = sum(source_counts.values())
        self.stdout.write(self.style.SUCCESS(
            f"Copie verifiee : {total} lignes metier. Sauvegarde SQLite conservee dans {backup_path.name}."
        ))
        self.stdout.write("Les fichiers du dossier media/ doivent etre transferes separement.")
