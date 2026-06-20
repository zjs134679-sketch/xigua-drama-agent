from __future__ import annotations

import base64
import gzip
import json

import httpx

from app.core.config import settings
from app.services.compliance import check, dictionary
from app.services.compliance import sync as compliance_sync


class FakeResponse:
    def __init__(self, data: dict):
        self.data = data

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict:
        return self.data


def payload(red: list[str], yellow: list[str]) -> str:
    raw = json.dumps({"red": red, "yellow": yellow}, ensure_ascii=False).encode("utf-8")
    return base64.b64encode(gzip.compress(raw, mtime=0)).decode("ascii")


def test_sync_installs_local_dictionary_and_reloads(monkeypatch, tmp_path):
    response = {
        "version": "test-v1",
        "encoding": "gzip+base64",
        "payload": payload(["云端测试红线词|测试"], ["云端测试黄线词|测试"]),
    }

    try:
        with monkeypatch.context() as scoped:
            scoped.setattr(settings, "dict_dir", tmp_path)
            scoped.setattr(compliance_sync.httpx, "get", lambda *_args, **_kwargs: FakeResponse(response))
            dictionary.reload()

            result = compliance_sync.sync_dictionary()

            assert result["synced"] is True
            assert (tmp_path / "version.local.txt").read_text(encoding="utf-8").strip() == "test-v1"
            assert check("这里有云端测试红线词").blocked
            assert check("这里有云端测试黄线词").warn
    finally:
        dictionary.reload()


def test_sync_network_failure_is_silent(monkeypatch, tmp_path):
    request = httpx.Request("GET", "http://example.invalid/compliance/dict")

    def unavailable(*_args, **_kwargs):
        raise httpx.ConnectError("offline", request=request)

    with monkeypatch.context() as scoped:
        scoped.setattr(settings, "dict_dir", tmp_path)
        scoped.setattr(compliance_sync.httpx, "get", unavailable)

        result = compliance_sync.sync_dictionary()

        assert result == {"synced": False, "unchanged": False, "version": None}

    dictionary.reload()
