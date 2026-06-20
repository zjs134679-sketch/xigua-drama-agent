"""算力节点：本地 ComfyUI / 国内远程主机 / 云 API（统一抽象）。"""
from app.services.compute.base import ComputeNode, ImageJob, JobResult  # noqa: F401
from app.services.compute.cloud_api import CloudApiNode  # noqa: F401
from app.services.compute.registry import get_active_node  # noqa: F401
