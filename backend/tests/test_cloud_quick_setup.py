"""快速云出片 / 节点能力选择（无真实外网调用）。"""
from __future__ import annotations

import asyncio
import base64
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.core.db import Base
from app.models.system import ComputeNode as ComputeNodeRow
from app.services.cloud_quick_setup import quick_setup_cloud
from app.services.compute.base import ImageJob
from app.services.compute.cloud_api import SeedanceAdapter, resolve_cloud_image_ref
from app.services.compute.registry import describe_compute_readiness, get_active_node, node_supports


def make_db() -> Session:
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    return Session(engine)


def test_node_supports_by_provider():
    wan = ComputeNodeRow(
        name="w",
        type="cloud_api",
        base_url="https://example",
        provider="wan",
        is_active=True,
        priority=10,
    )
    seed = ComputeNodeRow(
        name="s",
        type="cloud_api",
        base_url="https://example",
        provider="seedance",
        is_active=True,
        priority=10,
    )
    assert node_supports(wan, "image") is True
    assert node_supports(wan, "video") is False
    assert node_supports(seed, "video") is True
    assert node_supports(seed, "image") is False


def test_get_active_node_picks_by_capability():
    db = make_db()
    db.add_all(
        [
            ComputeNodeRow(
                name="seed",
                type="cloud_api",
                base_url="https://ark.example",
                provider="seedance",
                api_key="k",
                priority=200,
                is_active=True,
                capabilities='["video"]',
            ),
            ComputeNodeRow(
                name="wan",
                type="cloud_api",
                base_url="https://dash.example",
                provider="wan",
                api_key="k",
                priority=100,
                is_active=True,
                capabilities='["image"]',
            ),
        ]
    )
    db.commit()
    img = get_active_node(db, capability="image")
    vid = get_active_node(db, capability="video")
    assert getattr(img, "provider", None) == "wan"
    assert getattr(vid, "provider", None) == "seedance"
    db.close()


def test_quick_setup_creates_two_nodes():
    db = make_db()

    async def _run():
        return await quick_setup_cloud(
            db,
            wan_api_key="wan-placeholder-key",
            seedance_api_key="seed-placeholder-key",
            test_connection=False,
        )

    result = asyncio.run(_run())
    assert len(result["nodes"]) == 2
    readiness = describe_compute_readiness(db)
    assert readiness["cloud_ready"] is True
    assert readiness["has_image_path"] and readiness["has_video_path"]
    db.close()


def test_seedance_duration_from_image_job():
    adapter = SeedanceAdapter()
    spec = adapter.build_request(
        "m",
        ImageJob(
            prompt="动",
            width=720,
            height=1280,
            reference_images=["https://files.example/a.png"],
            duration=4,
        ),
    )
    assert spec.json is not None
    assert spec.json["duration"] == 4


def test_resolve_local_oss_to_data_url(tmp_path: Path):
    png = tmp_path / "shot.png"
    # 最小合法 PNG 头不够严格解析，但 base64 包装即可
    png.write_bytes(b"\x89PNG\r\n\x1a\nfake")
    url = resolve_cloud_image_ref(f"/oss/{png.name}", tmp_path)
    assert url.startswith("data:image/")
    assert ";base64," in url
    # 可解码
    b64 = url.split(",", 1)[1]
    assert base64.b64decode(b64)[:4] == b"\x89PNG"
