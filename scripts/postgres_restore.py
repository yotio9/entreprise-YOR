"""Restore a custom-format backup with explicit destructive confirmation."""
import argparse
import os
import subprocess
import sys
from pathlib import Path
import dj_database_url


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("backup", type=Path)
    parser.add_argument("--confirm-overwrite", action="store_true")
    args = parser.parse_args()
    if not args.confirm_overwrite:
        parser.error("restore replaces database objects; pass --confirm-overwrite after a fresh backup")
    if not args.backup.is_file() or args.backup.suffix != ".dump":
        parser.error("backup must be an existing .dump file")
    url = os.environ.get("DATABASE_URL", "").strip()
    if not url:
        print("DATABASE_URL is required.", file=sys.stderr)
        return 2
    try:
        config = dj_database_url.parse(url, ssl_require=True)
        if config.get("ENGINE") != "django.db.backends.postgresql":
            raise ValueError("not PostgreSQL")
        env = os.environ.copy()
        env.update({
            "PGHOST": str(config["HOST"]),
            "PGPORT": str(config.get("PORT") or "5432"),
            "PGUSER": str(config["USER"]),
            "PGPASSWORD": str(config["PASSWORD"]),
            "PGDATABASE": str(config["NAME"]),
            "PGSSLMODE": str(config.get("OPTIONS", {}).get("sslmode", "require")),
        })
        result = subprocess.run(
            ["pg_restore", "--dbname", str(config["NAME"]), "--clean", "--if-exists", "--no-owner", "--no-acl", str(args.backup)],
            env=env, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL, check=False,
        )
        if result.returncode:
            print("Restore failed; verify compatibility and the target database.", file=sys.stderr)
            return result.returncode
        print("Restore completed.")
        return 0
    except FileNotFoundError:
        print("pg_restore is not installed on this machine.", file=sys.stderr)
        return 2
    except Exception as exc:
        print("Restore failed (" + type(exc).__name__ + ").", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
