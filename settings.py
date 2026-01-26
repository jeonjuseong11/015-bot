from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Optional

ROOT_DIR = Path(__file__).resolve().parent

try:
    import local_settings as _local_settings  # type: ignore
except ImportError:  # pragma: no cover
    _local_settings = None


def _env(name: str, default: Optional[str] = None) -> Optional[str]:
    value = os.getenv(name)
    if value is None or value == "":
        return default
    return value


def _env_int(name: str, default: Optional[int] = None) -> Optional[int]:
    value = _env(name)
    if value is None:
        return default
    try:
        return int(value)
    except ValueError as exc:  # pragma: no cover
        raise ValueError(f"Environment variable {name} must be an int, got {value!r}") from exc


def _local(name: str, default: Any = None) -> Any:
    if _local_settings is None:
        return default
    return getattr(_local_settings, name, default)


# ==============================================================================
# Google Sheets
# ==============================================================================

JSON_KEY_FILE = _env(
    "GOOGLE_JSON_KEY_FILE",
    _local("JSON_KEY_FILE", str(ROOT_DIR / "service_account.json")),
)
SHEET_ID = _env("GOOGLE_SHEET_ID", _local("SHEET_ID", ""))
TAB_NAME = _env("GOOGLE_SHEET_TAB", _local("TAB_NAME", "데이터입력시트"))


# ==============================================================================
# Databases
# ==============================================================================

YH_DB_CONFIG = _local(
    "YH_DB_CONFIG",
    {
        "host": _env("YH_DB_HOST", ""),
        "port": _env_int("YH_DB_PORT", 5432),
        "user": _env("YH_DB_USER", ""),
        "password": _env("YH_DB_PASSWORD", ""),
        "database": _env("YH_DB_DATABASE", ""),
        "connect_timeout": _env_int("YH_DB_CONNECT_TIMEOUT", 10),
    },
)

HDB_CONFIG = _local(
    "HDB_CONFIG",
    {
        "host": _env("HDB_HOST", ""),
        "port": _env_int("HDB_PORT", 3306),
        "user": _env("HDB_USER", ""),
        "password": _env("HDB_PASSWORD", ""),
        "database": _env("HDB_DATABASE"),
        "charset": _env("HDB_CHARSET", "utf8"),
        "connect_timeout": _env_int("HDB_CONNECT_TIMEOUT", 10),
    },
)


def require_google_settings() -> None:
    issues: list[str] = []

    if not SHEET_ID:
        issues.append("Missing SHEET_ID (set GOOGLE_SHEET_ID or local_settings.SHEET_ID).")

    if not JSON_KEY_FILE:
        issues.append(
            "Missing JSON_KEY_FILE (set GOOGLE_JSON_KEY_FILE or local_settings.JSON_KEY_FILE)."
        )
    elif not Path(JSON_KEY_FILE).expanduser().exists():
        issues.append(f"JSON_KEY_FILE not found: {JSON_KEY_FILE!r}")

    if issues:
        raise RuntimeError("Google settings error:\n- " + "\n- ".join(issues))


def require_db_settings() -> None:
    issues: list[str] = []

    for key in ("host", "user", "password", "database"):
        if not str(YH_DB_CONFIG.get(key, "")).strip():
            issues.append(
                f"Missing YH_DB_CONFIG[{key!r}] (set YH_DB_{key.upper()} or local_settings.YH_DB_CONFIG)."
            )

    for key in ("host", "user", "password"):
        if not str(HDB_CONFIG.get(key, "")).strip():
            issues.append(
                f"Missing HDB_CONFIG[{key!r}] (set HDB_{key.upper()} or local_settings.HDB_CONFIG)."
            )

    if issues:
        raise RuntimeError("Database settings error:\n- " + "\n- ".join(issues))
