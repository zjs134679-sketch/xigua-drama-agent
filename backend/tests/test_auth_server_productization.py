from __future__ import annotations

import importlib.util
import sys
from functools import lru_cache
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient


@lru_cache(maxsize=1)
def load_auth_app() -> FastAPI:
    module_path = Path(__file__).resolve().parents[2] / "auth-server" / "app" / "main.py"
    spec = importlib.util.spec_from_file_location("xigua_auth_server_main", module_path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module.app


def test_auth_server_cors_allows_desktop_frontend() -> None:
    client = TestClient(load_auth_app())
    for origin in ("http://localhost:5173", "tauri://localhost"):
        response = client.options(
            "/version",
            headers={
                "Origin": origin,
                "Access-Control-Request-Method": "GET",
            },
        )

        assert response.status_code == 200
        assert response.headers["access-control-allow-origin"] == origin
        assert "GET" in response.headers["access-control-allow-methods"]


def test_auth_server_version_response_shape() -> None:
    client = TestClient(load_auth_app())
    response = client.get("/version")

    assert response.status_code == 200
    payload = response.json()
    assert set(payload) == {"latest", "url", "notes"}
    assert all(isinstance(payload[key], str) for key in ("latest", "url", "notes"))
