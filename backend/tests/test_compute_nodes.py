"""算力节点协议测试只使用占位地址和临时凭据。"""
from __future__ import annotations

import asyncio
import json
from pathlib import Path

import httpx

from app.api.compute import _node_payload
from app.models.system import ComputeNode as ComputeNodeRow
from app.services.compute import cloud_api
from app.services.compute.base import ImageJob
from app.services.compute.cloud_api import CloudApiNode, SeedanceAdapter, WanAdapter
from app.services.compute.local_comfy import LocalComfyNode
from app.services.compute.registry import build_node
from app.services.compute.remote_comfy import RemoteComfyNode


def test_wan_request_and_async_result_are_dispatched(monkeypatch, tmp_path):
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.path.endswith("/image-synthesis"):
            body = json.loads(request.content)
            assert request.headers["authorization"] == "Bearer placeholder-key"
            assert request.headers["x-dashscope-async"] == "enable"
            assert body["model"] == "wan-model-placeholder"
            assert body["input"]["prompt"] == "占位画面"
            assert body["parameters"]["size"] == "640*960"
            return httpx.Response(200, json={"output": {"task_id": "task-placeholder"}})
        if request.url.path.endswith("/tasks/task-placeholder"):
            return httpx.Response(
                200,
                json={
                    "output": {
                        "task_status": "SUCCEEDED",
                        "results": [{"url": "https://files.example/generated.png"}],
                    }
                },
            )
        if request.url.host == "files.example":
            return httpx.Response(200, content=b"placeholder-image", headers={"content-type": "image/png"})
        raise AssertionError(f"unexpected request: {request.url}")

    transport = httpx.MockTransport(handler)
    real_client = httpx.AsyncClient

    def mock_client(*args, **kwargs):
        return real_client(transport=transport, *args, **kwargs)

    monkeypatch.setattr(cloud_api.httpx, "AsyncClient", mock_client)
    monkeypatch.setattr(cloud_api.settings, "data_dir", tmp_path)

    node = CloudApiNode(
        "wan",
        "https://api.example",
        "placeholder-key",
        "wan-model-placeholder",
    )
    result = asyncio.run(node.text2image(ImageJob(prompt="占位画面", width=640, height=960)))

    assert isinstance(node.adapter, WanAdapter)
    assert result.status == "completed"
    assert result.meta["provider"] == "wan"
    assert result.image_path and Path(result.image_path).exists()
    assert len(requests) == 3


def test_seedance_provider_builds_video_task_request():
    node = CloudApiNode("seedance", api_key="placeholder-key")
    assert isinstance(node.adapter, SeedanceAdapter)

    spec = node.adapter.build_request(
        node.model,
        ImageJob(prompt="占位动态画面", width=720, height=1280, reference_images=["https://files.example/ref.png"]),
    )

    assert spec.path == "/api/v3/contents/generations/tasks"
    assert spec.json["ratio"] == "9:16"
    assert spec.json["content"][1]["type"] == "image_url"


def test_registry_builds_all_node_types():
    local = build_node(ComputeNodeRow(name="local", type="local_comfy", base_url="http://local.example"))
    remote = build_node(
        ComputeNodeRow(name="remote", type="remote_comfy", base_url="https://remote.example", token="placeholder-token")
    )
    cloud = build_node(
        ComputeNodeRow(
            name="cloud",
            type="cloud_api",
            base_url="https://cloud.example",
            provider="wan",
            api_key="placeholder-key",
            model="placeholder-model",
        )
    )

    assert type(local) is LocalComfyNode
    assert type(remote) is RemoteComfyNode
    assert type(cloud) is CloudApiNode
    assert cloud.provider == "wan"
    assert cloud.model == "placeholder-model"


def test_remote_reference_image_uses_authenticated_multipart(tmp_path):
    source = tmp_path / "reference.png"
    source.write_bytes(b"placeholder-image")

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/upload/image"
        assert request.headers["authorization"] == "Bearer placeholder-token"
        assert "multipart/form-data" in request.headers["content-type"]
        assert b'reference.png' in request.content
        assert b'placeholder-image' in request.content
        return httpx.Response(200, json={"name": "uploaded-reference.png"})

    async def upload() -> list[str]:
        node = RemoteComfyNode("https://remote.example", "placeholder-token")
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            return await node._upload_reference_images(client, [str(source)])

    assert asyncio.run(upload()) == ["uploaded-reference.png"]


def test_node_payload_never_returns_credentials():
    row = ComputeNodeRow(
        id=1,
        name="cloud",
        type="cloud_api",
        base_url="https://cloud.example",
        provider="wan",
        api_key="placeholder-key",
        token="placeholder-token",
        model="placeholder-model",
        priority=100,
        is_active=True,
    )

    payload = _node_payload(row)

    assert "api_key" not in payload
    assert "token" not in payload
    assert payload["api_key_configured"] is True
    assert payload["token_configured"] is True
