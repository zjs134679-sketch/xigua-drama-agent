"""分镜 Agent —— 用 xg_shot_break skill 把剧本拆解为分镜清单。"""
from __future__ import annotations

import json
import re
import time

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.domain import Drama, Episode, Storyboard, StoryboardCharacter
from app.services.agents.script_agent import load_skill
from app.services.llm.client import chat_text, resolve_llm
from app.services.storyboard_references import match_storyboard_scene, sync_storyboard_characters

# 单次 LLM 输出易被截断：按场次分块，每块控制在可完整返回的范围内
_CHUNK_SOFT_CHARS = 1400
_MAX_OUTPUT_TOKENS = 8192
# 单块失败后的单独重试次数（指数退避 2s、4s）；client.chat 本身已有 429/5xx 重试，
# 这里主要兜底 JSON 截断等单块偶发失败，避免整单 token 作废
_CHUNK_MAX_RETRIES = 2

_OUTPUT_SPEC = (
    "\n\n把用户剧本拆成分镜清单。所有提示词字段必须使用中文，不要输出英文提示词。**只输出 JSON**，格式："
    '{"storyboards":[{'
    '"storyboard_number":1,'
    '"title":"3到5个字的中文标题",'
    '"location":"中文地点",'
    '"time":"中文时间和光线",'
    '"shot_type":"中文景别（远景/全景/中景/近景/特写/大特写）",'
    '"angle":"中文机位角度",'
    '"movement":"中文镜头运动",'
    '"action":"谁做什么+表情动作，中文",'
    '"dialogue":"中文台词（有人说话必须写「角色名：台词」原文；无人说话填空字符串；禁止把台词只写在 video_prompt 里）",'
    '"result":"中文画面结果",'
    '"atmosphere":"中文光线/色调/气氛",'
    '"image_prompt":"中文静态画面提示词，纯视觉描述，包含景别、构图、人物动作、环境、光线、气氛，不要真实人名",'
    '"video_prompt":"中文视频提示词，时间片必须落在 0–duration 秒内（例如 duration=3 则只写 0-3秒：…），禁止写超出 duration 的区间；不要写具体台词原文（台词只放 dialogue）",'
    '"bgm_prompt":"中文配乐风格",'
    '"sound_effect":"中文关键音效",'
    '"duration":5'
    "}]}. duration 是 3 到 5 的整数（本地 ComfyUI 图生视频最长 5 秒，禁止 10–15 秒长镜）。"
    "video_prompt 的时间轴总长必须等于该镜 duration，不得写 0-5秒 却把 duration 标成 3。"
    "台词过长必须拆成多个连续镜头。本段场次镜头宜精炼（通常 4–12 镜），不要为凑数过度拆镜。"
    "对白镜头 dialogue 必填「角色名：中文台词」且与剧本一致；空镜/无对白 dialogue 必须是 \"\"。"
    "image_prompt 必须是可直接出图的中文视觉描述，并写入系统给定的画风锚词；静帧不要写 0-N秒 时间片。"
    "不要解释，不要前后缀，不要输出 markdown 代码块。"
)


def _ensure_style_anchors(shots: list[dict], anchors: list[str], suffix: str = "") -> None:
    """确定性补全 image_prompt 中缺失的画风锚词（不依赖二次 LLM）。"""
    if not anchors and not suffix:
        return
    for shot in shots:
        if not isinstance(shot, dict):
            continue
        prompt = (shot.get("image_prompt") or "").strip()
        missing = [a for a in anchors if a and a not in prompt]
        tail_bits: list[str] = []
        if missing:
            tail_bits.append("，".join(missing))
        if suffix and suffix not in prompt:
            tail_bits.append(suffix[:80])
        if tail_bits:
            shot["image_prompt"] = (prompt + "，" + "，".join(tail_bits)).strip("，")


def _split_script_chunks(script_content: str, soft_chars: int = _CHUNK_SOFT_CHARS) -> list[str]:
    """按「## 场次」/分隔线切分剧本；过长单场再按段落合并到 soft_chars 以内。"""
    text = (script_content or "").strip()
    if not text:
        return []

    # 标题/前言与各场次：优先按「## 场次」切开
    parts = re.split(r"(?=^##\s*场次)", text, flags=re.MULTILINE)
    parts = [p.strip() for p in parts if p.strip()]

    if len(parts) <= 1:
        parts = [p.strip() for p in re.split(r"\n-{3,}\n", text) if p.strip()]

    if len(parts) <= 1:
        if len(text) <= soft_chars * 2:
            return [text]
        # 无场次结构的长文：按空行段落硬切
        paragraphs = [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]
        parts = paragraphs or [text]

    # 首块若是整集标题且很短，并入下一场
    if len(parts) >= 2 and len(parts[0]) < 200 and not re.search(r"^##\s*场次", parts[0], re.MULTILINE):
        parts = [parts[0] + "\n\n" + parts[1], *parts[2:]]

    chunks: list[str] = []
    buf = ""
    for part in parts:
        if not buf:
            buf = part
            continue
        # 单场已超 soft 且 buf 非空：先落盘
        if len(part) > soft_chars and buf:
            chunks.append(buf)
            buf = part
            continue
        if len(buf) + 2 + len(part) <= soft_chars:
            buf = buf + "\n\n" + part
        else:
            chunks.append(buf)
            buf = part
    if buf:
        chunks.append(buf)

    # 超长单块再按段落二次切开
    final: list[str] = []
    for chunk in chunks:
        if len(chunk) <= soft_chars * 2:
            final.append(chunk)
            continue
        paras = [p.strip() for p in re.split(r"\n\s*\n", chunk) if p.strip()]
        sub = ""
        for p in paras:
            if not sub:
                sub = p
            elif len(sub) + 2 + len(p) <= soft_chars:
                sub = sub + "\n\n" + p
            else:
                final.append(sub)
                sub = p
        if sub:
            final.append(sub)
    return final or [text]


def _strip_code_fence(raw: str) -> str:
    text = (raw or "").strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text, count=1, flags=re.IGNORECASE)
        text = re.sub(r"\s*```\s*$", "", text)
    return text.strip()


def _parse_storyboards_json(raw: str) -> list[dict]:
    text = _strip_code_fence(raw)
    if not text:
        raise ValueError("分镜结果为空")

    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        # 常见原因：单次输出超 max_tokens 被截断
        hint = ""
        if "Unterminated string" in str(exc) or "Expecting" in str(exc):
            hint = "（多为模型输出被截断；已按场次分块重试仍失败时请缩短剧本或减少单场对白）"
        raise ValueError(f"分镜 JSON 解析失败{hint}: {exc}") from exc

    if not isinstance(data, dict):
        raise ValueError("分镜返回格式不是对象")
    shots = data.get("storyboards") or data.get("shots") or []
    if not isinstance(shots, list):
        raise ValueError("分镜返回格式不是列表")
    return [s for s in shots if isinstance(s, dict)]


def _break_one_chunk(
    script_chunk: str,
    *,
    base_url: str,
    api_key: str,
    model: str,
    skill: str,
    temperature: float,
    chunk_index: int,
    chunk_total: int,
    style_prefix: str = "",
) -> list[dict]:
    header = ""
    if chunk_total > 1:
        header = (
            f"这是整集剧本的第 {chunk_index}/{chunk_total} 段（按场次切分）。"
            f"只为本段拆分镜，不要补写其它场次。\n\n"
        )
    messages = [
        {"role": "system", "content": skill + style_prefix + _OUTPUT_SPEC},
        {"role": "user", "content": header + script_chunk},
    ]
    raw = chat_text(
        messages,
        base_url,
        api_key,
        model,
        temperature=temperature,
        response_format={"type": "json_object"},
        max_tokens=_MAX_OUTPUT_TOKENS,
        timeout=180.0,
    )
    return _parse_storyboards_json(raw)


def break_storyboards(
    db: Session,
    script_content: str,
    temperature: float = 0.4,
    *,
    episode_id: int | None = None,
    drama_id: int | None = None,
) -> list[dict]:
    from app.models.domain import Drama, Episode
    from app.services.style_composer import compose
    from app.services.style_contract import STAGE_SHOT_BREAK

    base_url, api_key, model = resolve_llm(db)
    skill = load_skill("xg_shot_break")
    chunks = _split_script_chunks(script_content)
    if not chunks:
        raise ValueError("剧本内容为空")

    style_prefix = ""
    anchors: list[str] = []
    suffix = ""
    try:
        drama = None
        if drama_id is not None:
            drama = db.get(Drama, drama_id)
        elif episode_id is not None:
            ep = db.get(Episode, episode_id)
            if ep is not None:
                drama = db.get(Drama, ep.drama_id)
        contract = compose(db=db, drama=drama, stage=STAGE_SHOT_BREAK)
        style_prefix = "\n\n" + contract.system_prefix()
        if contract.must_include:
            style_prefix += (
                "\n每条 image_prompt 必须包含这些画风锚词："
                + "、".join(contract.must_include[:10])
            )
        anchors = list(contract.must_include or [])
        suffix = (contract.prompt_suffix or "").strip()
    except Exception:  # noqa: BLE001
        style_prefix = ""

    all_shots: list[dict] = []
    for index, chunk in enumerate(chunks, start=1):
        shots: list[dict] | None = None
        last_err: Exception | None = None
        # 失败分块单独重试：之前成功的块不受影响，不再整单作废
        for attempt in range(1 + _CHUNK_MAX_RETRIES):
            try:
                shots = _break_one_chunk(
                    chunk,
                    base_url=base_url,
                    api_key=api_key,
                    model=model,
                    skill=skill,
                    temperature=temperature,
                    chunk_index=index,
                    chunk_total=len(chunks),
                    style_prefix=style_prefix,
                )
                break
            except Exception as exc:  # noqa: BLE001 - 429/5xx/超时/截断都值得再试
                last_err = exc
                if attempt < _CHUNK_MAX_RETRIES:
                    time.sleep(2.0 * (2**attempt))
        if shots is None:
            raise ValueError(
                f"分镜生成失败：第 {index}/{len(chunks)} 块在"
                f" {1 + _CHUNK_MAX_RETRIES} 次尝试后仍失败"
                f"（已成功 {index - 1} 块）：{last_err}"
            )
        all_shots.extend(shots)

    # 全局重编号，避免分块后 number 冲突
    for i, shot in enumerate(all_shots, start=1):
        shot["storyboard_number"] = i

    _ensure_style_anchors(all_shots, anchors, suffix)
    _normalize_shot_dialogues(all_shots)

    # duration 与 video_prompt 时间片对齐（Comfy 2–5s）
    from app.services.duration_align import align_shots

    all_shots = align_shots([s for s in all_shots if isinstance(s, dict)])

    if not all_shots:
        raise ValueError("分镜结果为空：模型未返回任何镜头")
    return all_shots


def _normalize_shot_dialogues(shots: list[dict]) -> None:
    """把散落在 video_prompt/action 的「角色：台词」收回 dialogue 字段。"""
    from app.services.video_generation import extract_dialogue_from_shot_fields

    for shot in shots:
        if not isinstance(shot, dict):
            continue
        dialogue = extract_dialogue_from_shot_fields(
            shot.get("dialogue") if isinstance(shot.get("dialogue"), str) else None,
            shot.get("video_prompt") if isinstance(shot.get("video_prompt"), str) else None,
            shot.get("action") if isinstance(shot.get("action"), str) else None,
            shot.get("image_prompt") if isinstance(shot.get("image_prompt"), str) else None,
        )
        shot["dialogue"] = dialogue


def _int(value: object, default: int = 0) -> int:
    try:
        return int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return default


def save_storyboards(db: Session, episode_id: int, shots: list[dict]) -> int:
    """整集重建分镜：清掉旧的，按新清单插入。返回新建数量。"""
    old = db.scalars(
        select(Storyboard).where(Storyboard.episode_id == episode_id, Storyboard.deleted_at.is_(None))
    ).all()
    old_ids = [row.id for row in old]
    if old_ids:
        links = db.scalars(select(StoryboardCharacter).where(StoryboardCharacter.storyboard_id.in_(old_ids))).all()
        for link in links:
            db.delete(link)
    for row in old:
        db.delete(row)

    count = 0
    for index, shot in enumerate(shots, start=1):
        if not isinstance(shot, dict):
            continue
        row = Storyboard(
                episode_id=episode_id,
                storyboard_number=_int(shot.get("storyboard_number"), index),
                title=shot.get("title"),
                location=shot.get("location"),
                time=shot.get("time"),
                shot_type=shot.get("shot_type"),
                angle=shot.get("angle"),
                movement=shot.get("movement"),
                action=shot.get("action"),
                result=shot.get("result"),
                atmosphere=shot.get("atmosphere"),
                image_prompt=shot.get("image_prompt"),
                video_prompt=shot.get("video_prompt"),
                bgm_prompt=shot.get("bgm_prompt"),
                sound_effect=shot.get("sound_effect"),
                dialogue=shot.get("dialogue"),
                duration=max(3, min(_int(shot.get("duration"), 5), 5)),
                status="pending",
            )
        # 入库前再对齐一次 video_prompt（防漏）
        from app.services.duration_align import align_shot_dict

        aligned = align_shot_dict(
            {
                "duration": row.duration,
                "video_prompt": row.video_prompt,
                "image_prompt": row.image_prompt,
            }
        )
        row.duration = aligned.get("duration") or row.duration
        row.video_prompt = aligned.get("video_prompt")
        if aligned.get("image_prompt"):
            row.image_prompt = aligned["image_prompt"]
        db.add(row)
        db.flush()
        sync_storyboard_characters(db, row)
        # 立刻绑定场景资产，方便后续出图拿场景参考
        scene = match_storyboard_scene(db, row)
        if scene is not None:
            row.scene_id = scene.id
        count += 1
    db.commit()
    # 新建分镜后按场景连续性生成运镜段落
    from app.services.storyboard_segments import refresh_episode_segments

    refresh_episode_segments(db, episode_id, commit=True)
    return count


def polish_prompts(
    db: Session,
    shots: list[dict],
    temperature: float = 0.4,
    *,
    episode_id: int | None = None,
) -> list[dict]:
    """用 LLM 为现有分镜生成中文画面提示词。"""
    base_url, api_key, model = resolve_llm(db)
    skill = load_skill("xg_shot_break")
    style_note = ""
    anchors: list[str] = []
    suffix = ""
    try:
        from app.services.style_composer import compose
        from app.services.style_contract import STAGE_SHOT_IMAGE

        drama = None
        if episode_id is not None:
            ep = db.get(Episode, episode_id)
            if ep is not None:
                drama = db.get(Drama, ep.drama_id)
        contract = compose(db=db, drama=drama, stage=STAGE_SHOT_IMAGE)
        style_note = "\n\n" + contract.system_prefix()
        anchors = list(contract.must_include or [])
        suffix = (contract.prompt_suffix or "").strip()
        if anchors:
            style_note += "\n每条 prompt 必须包含画风锚词：" + "、".join(anchors[:10])
    except Exception:  # noqa: BLE001
        style_note = ""
    prompt = (
        "下面是分镜数据（JSON 数组）。请为每个分镜生成高质量中文静态画面提示词 image_prompt。"
        "提示词要包含景别、机位、构图、人物动作、表情、环境、光线和气氛，可直接用于 AI 出图。"
        "必须输出中文，不要英文，不要中英混杂。"
        "\n\n**只输出 JSON**，格式：{\"prompts\":[{\"number\":镜头编号,\"prompt\":\"中文画面提示词\"}]}。不要解释。"
        f"\n\n分镜数据：\n{json.dumps(shots, ensure_ascii=False)}"
    )
    messages = [
        {"role": "system", "content": skill + style_note},
        {"role": "user", "content": prompt},
    ]
    raw = chat_text(
        messages,
        base_url,
        api_key,
        model,
        temperature=temperature,
        response_format={"type": "json_object"},
        max_tokens=_MAX_OUTPUT_TOKENS,
    )
    text = _strip_code_fence(raw)
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ValueError(f"润色提示词 JSON 解析失败: {exc}") from exc
    prompts = data.get("prompts") or []
    if not isinstance(prompts, list):
        raise ValueError("润色提示词结果不是列表")
    # 确定性补锚词
    for item in prompts:
        if not isinstance(item, dict):
            continue
        p = (item.get("prompt") or "").strip()
        missing = [a for a in anchors if a and a not in p]
        bits = []
        if missing:
            bits.append("，".join(missing))
        if suffix and suffix not in p:
            bits.append(suffix[:80])
        if bits:
            item["prompt"] = (p + "，" + "，".join(bits)).strip("，")
    return prompts
