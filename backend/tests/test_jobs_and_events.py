"""任务队列 / 事件启发式 / 错误分类 / 模型地图 单测。"""
from __future__ import annotations

from app.services.comfy_errors import classify_comfy_error
from app.services.novel_events import _heuristic_events, _parse_json_list
from app.services.project_memory import DEFAULT_MODEL_MAP


def test_classify_oom():
    code, msg = classify_comfy_error("CUDA out of memory")
    assert code == "oom"
    assert "显存" in msg


def test_classify_missing_model():
    code, msg = classify_comfy_error("Model file not found: xxx.gguf")
    assert code == "missing_model"


def test_heuristic_events():
    text = "第一段内容很长很长很长很长。\n\n第二段也有足够长度。\n\n第三段继续。"
    events = _heuristic_events(text, max_events=5)
    assert len(events) >= 1
    assert "title" in events[0]


def test_parse_json_list():
    raw = '```json\n[{"title":"A","summary":"s"}]\n```'
    items = _parse_json_list(raw)
    assert len(items) == 1
    assert items[0]["title"] == "A"


def test_default_model_map():
    assert DEFAULT_MODEL_MAP["video_test_quality"] == "test"
    assert DEFAULT_MODEL_MAP["video_final_quality"] == "final"
