from __future__ import annotations

import base64
import gzip
import json
import tempfile
import threading
from pathlib import Path
from typing import Any

import httpx

from app.core.config import settings
from app.services.compliance.dictionary import dictionary

_SYNC_LOCK = threading.Lock()
_ENCODING = "gzip+base64"


def _version_path() -> Path:
    return settings.dict_dir / "version.local.txt"


def _read_version() -> str | None:
    if not all((settings.dict_dir / f"{name}.local.txt").exists() for name in ("red", "yellow")):
        return None
    try:
        version = _version_path().read_text(encoding="utf-8").strip()
        return version or None
    except OSError:
        return None


def _entries(value: Any) -> list[str]:
    if not isinstance(value, list):
        raise ValueError("invalid dictionary entries")
    entries: list[str] = []
    for item in value:
        if not isinstance(item, str):
            raise ValueError("invalid dictionary entry")
        entry = item.strip()
        if entry and "\n" not in entry and "\r" not in entry and not entry.startswith("#"):
            entries.append(entry)
    return entries


def _decode(payload: str) -> tuple[list[str], list[str]]:
    compressed = base64.b64decode(payload.encode("ascii"), validate=True)
    data = json.loads(gzip.decompress(compressed).decode("utf-8"))
    if not isinstance(data, dict):
        raise ValueError("invalid dictionary payload")
    return _entries(data.get("red")), _entries(data.get("yellow"))


def _stage_text(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    handle = tempfile.NamedTemporaryFile(
        mode="w",
        encoding="utf-8",
        dir=path.parent,
        prefix=f".{path.name}.",
        suffix=".tmp",
        delete=False,
    )
    try:
        with handle:
            handle.write(text)
            handle.flush()
        return Path(handle.name)
    except Exception:
        Path(handle.name).unlink(missing_ok=True)
        raise


def _install(red: list[str], yellow: list[str], version: str) -> None:
    targets = {
        settings.dict_dir / "red.local.txt": "\n".join(red) + ("\n" if red else ""),
        settings.dict_dir / "yellow.local.txt": "\n".join(yellow) + ("\n" if yellow else ""),
        _version_path(): version + "\n",
    }
    staged: dict[Path, Path] = {}
    try:
        for target, content in targets.items():
            staged[target] = _stage_text(target, content)
        for target, temporary in staged.items():
            temporary.replace(target)
    finally:
        for temporary in staged.values():
            temporary.unlink(missing_ok=True)


def sync_dictionary() -> dict:
    """Pull and activate the cloud dictionary; every failure falls back locally."""
    with _SYNC_LOCK:
        current = _read_version()
        try:
            response = httpx.get(
                f"{settings.auth_server_url.rstrip('/')}/compliance/dict",
                params={"since": current} if current else None,
                timeout=1.5,
            )
            response.raise_for_status()
            data = response.json()
            if not isinstance(data, dict):
                raise ValueError("invalid sync response")
            version = data.get("version")
            if not isinstance(version, str) or not version.strip():
                raise ValueError("invalid dictionary version")
            version = version.strip()
            payload = data.get("payload")
            if payload is None and version == current:
                return {"synced": False, "unchanged": True, "version": current}
            if data.get("encoding") != _ENCODING or not isinstance(payload, str):
                raise ValueError("invalid dictionary encoding")
            red, yellow = _decode(payload)
            _install(red, yellow, version)
            dictionary.reload()
            return {"synced": True, "unchanged": False, "version": version, **dictionary.stats()}
        except Exception:
            return {"synced": False, "unchanged": False, "version": current}
