"""ComfyUI / 视频生成错误分类 —— 把原始异常变成可读中文 + 错误码。"""
from __future__ import annotations


def classify_comfy_error(raw: str | None) -> tuple[str, str]:
    """返回 (error_code, user_message)。"""
    text = (raw or "").strip() or "未知错误"
    low = text.lower()

    if any(k in low for k in ("out of memory", "oom", "cuda out of memory", "allocation on device")):
        return (
            "oom",
            "显存不足（OOM）。请降低分辨率/时长，或关闭其他占显存程序。",
        )
    if any(k in low for k in ("not found", "no such file", "missing", "file does not exist")) and any(
        k in low for k in ("model", "unet", "checkpoint", "lora", "vae", "clip", "gguf")
    ):
        return (
            "missing_model",
            "ComfyUI 缺少模型文件。请在算力页运行「模型检测」，对照工作流补齐 LTX/Wan/Flux 权重。",
        )
    if "工作流模板不存在" in text or "workflow" in low and "not found" in low:
        return ("missing_workflow", f"工作流模板缺失：{text}")
    if any(k in low for k in ("timeout", "超时", "timed out")):
        return ("timeout", "生成超时。可缩短单镜时长、换测试档，或检查 ComfyUI 是否卡死。")
    if any(k in low for k in ("connection", "connect", "refused", "连接", "unreachable")):
        return (
            "connection",
            "无法连接 ComfyUI。请确认本机/远程节点已启动且算力页地址正确。",
        )
    if "不支持音频驱动" in text or "audio-driven" in low:
        return ("no_lip_sync", text)
    if "红线" in text or "compliance" in low or "blocked" in low:
        return ("compliance", "内容触发合规红线，已拦截。")
    if "没有镜头图" in text or "没有配音" in text:
        return ("precondition", text)
    return ("unknown", text[:500])
