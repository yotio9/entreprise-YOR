"""Create a private PostgreSQL backup without credentials in argv."""
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
import dj_database_url


def main():
    url = os.environ.get("DATABASE_URL", "").strip()
    if not url:
        print("DATABASE_URL is required.", file=sys.stderr)
        return 2
    path = None
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
        backup_dir = Path(__file__).resolve().parents[1] / ".private_backups"
        backup_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
        path = backup_dir / ("yorwani-postgres-" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + ".dump")
        result = subprocess.run(
            ["pg_dump", "--format=custom", "--no-owner", "--no-acl", "--file", str(path)],
            env=env, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL, check=False,
        )
        if result.returncode:
            path.unlink(missing_ok=True)
            print("pg_dump failed; verify the database connection and client version.", file=sys.stderr)
            return result.returncode
        try:
            os.chmod(path, 0o600)
        except OSError:
            pass
        print("Private backup created: " + path.name)
        return 0
    except FileNotFoundError:
        print("pg_dump is not installed on this machine.", file=sys.stderr)
        return 2
    except Exception as exc:
        if path:
            path.unlink(missing_ok=True)
        print("Backup failed (" + type(exc).__name__ + ").", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
