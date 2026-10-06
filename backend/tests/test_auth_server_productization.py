from __future__ import annotations

import importlib.util
import os
import sys
import uuid
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient


def load_auth_app(tmp_db: Path | None = None) -> FastAPI:
    """每次用独立模块名加载，避免缓存旧 schema；可选隔离 auth.db。"""
    if tmp_db is not None:
        # auth-server 用 BASE_DIR/auth.db；测试通过环境无法直接改路径时，
        # 在加载前把工作目录无关的绝对路径注入：补丁模块内 DB 太晚，
        # 这里用临时目录复制逻辑——直接改环境后在 main 支持 XIGUA_AUTH_DB。
        os.environ["XIGUA_AUTH_DB"] = str(tmp_db)
    os.environ.setdefault("XIGUA_LICENSE_REQUIRED", "1")
    # A5 修复后：auth-server 无有效密钥直接拒绝启动，测试先注入测试密钥
    os.environ.setdefault("XIGUA_AUTH_SECRET", "test-secret")
    os.environ.setdefault("XIGUA_ADMIN_SECRET", "test-admin-secret")
    # 授权服务已迁至独立卡密平台目录
    candidates = [
        Path(r"E:\xigua Agent  密码管理\auth-server\app\main.py"),
        Path(__file__).resolve().parents[2] / "auth-server" / "app" / "main.py",
    ]
    module_path = next((p for p in candidates if p.exists()), candidates[0])
    name = f"xigua_auth_server_main_{uuid.uuid4().hex}"
    spec = importlib.util.spec_from_file_location(name, module_path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module.app


def test_auth_server_cors_allows_desktop_frontend(tmp_path: Path) -> None:
    client = TestClient(load_auth_app(tmp_path / "cors.db"))
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


def test_auth_server_version_response_shape(tmp_path: Path) -> None:
    client = TestClient(load_auth_app(tmp_path / "ver.db"))
    response = client.get("/version")

    assert response.status_code == 200
    payload = response.json()
    assert set(payload) == {"latest", "url", "notes"}
    assert all(isinstance(payload[key], str) for key in ("latest", "url", "notes"))


def test_license_activate_flow(tmp_path: Path) -> None:
    """注册试用 → 管理员发卡 → 激活 → license_active。"""
    os.environ["XIGUA_LICENSE_REQUIRED"] = "1"
    os.environ["XIGUA_AUTH_SECRET"] = "test-secret"
    os.environ["XIGUA_ADMIN_SECRET"] = "test-admin"
    client = TestClient(load_auth_app(tmp_path / "lic.db"))
    uname = f"lic_{uuid.uuid4().hex[:8]}"
    reg = client.post(
        "/auth/register",
        json={"username": uname, "password": "pass1234", "machine_id": "dev-A"},
    )
    assert reg.status_code == 200, reg.text
    token = reg.json()["token"]
    user = reg.json()["user"]
    assert user["plan"] in ("trial", "free")
    assert "license_active" in user
    assert user["license_active"] is True  # 试用期内

    gen = client.post(
        "/admin/licenses",
        headers={"X-Admin-Secret": "test-admin"},
        json={"count": 1, "plan": "pro", "days": 30, "max_machines": 2},
    )
    assert gen.status_code == 200, gen.text
    code = gen.json()["codes"][0]

    act = client.post(
        "/license/activate",
        headers={"Authorization": f"Bearer {token}"},
        json={"code": code, "machine_id": "dev-A"},
    )
    assert act.status_code == 200, act.text
    activated = act.json()["user"]
    assert activated["plan"] == "pro"
    assert activated["license_active"] is True
    assert activated["machines_bound"] >= 1

    me = client.get(
        "/auth/me",
        headers={"Authorization": f"Bearer {token}", "X-Machine-Id": "dev-A"},
    )
    assert me.status_code == 200
    assert me.json()["license_active"] is True


def test_change_password_and_admin_reset(tmp_path: Path) -> None:
    """用户自助改密 + 管理员强制重置，共用 auth.db。"""
    os.environ["XIGUA_LICENSE_REQUIRED"] = "1"
    os.environ["XIGUA_AUTH_SECRET"] = "test-secret"
    os.environ["XIGUA_ADMIN_SECRET"] = "test-admin"
    client = TestClient(load_auth_app(tmp_path / "pw.db"))
    uname = f"pw_{uuid.uuid4().hex[:8]}"
    reg = client.post(
        "/auth/register",
        json={"username": uname, "password": "oldpass99", "machine_id": "dev-pw"},
    )
    assert reg.status_code == 200, reg.text

    bad = client.post(
        "/auth/change-password",
        json={"username": uname, "old_password": "wrong", "new_password": "newpass99"},
    )
    assert bad.status_code == 401

    ok = client.post(
        "/auth/change-password",
        json={"username": uname, "old_password": "oldpass99", "new_password": "newpass99"},
    )
    assert ok.status_code == 200, ok.text
    assert ok.json()["ok"] is True

    login_new = client.post(
        "/auth/login",
        json={"username": uname, "password": "newpass99", "machine_id": "dev-pw"},
    )
    assert login_new.status_code == 200, login_new.text

    login_old = client.post(
        "/auth/login",
        json={"username": uname, "password": "oldpass99", "machine_id": "dev-pw"},
    )
    assert login_old.status_code == 401

    admin = client.post(
        "/admin/reset-password",
        headers={"X-Admin-Secret": "test-admin"},
        json={"username": uname, "new_password": "adminset1"},
    )
    assert admin.status_code == 200, admin.text
    assert admin.json()["ok"] is True

    login_admin = client.post(
        "/auth/login",
        json={"username": uname, "password": "adminset1", "machine_id": "dev-pw"},
    )
    assert login_admin.status_code == 200, login_admin.text
