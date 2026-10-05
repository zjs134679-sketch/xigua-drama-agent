"""生产监督 —— 检查一集素材完备性（图/视频/配音/口型），给出可执行整改清单。"""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.domain import Character, Drama, Episode, Scene, Storyboard, StoryboardCharacter, VideoGeneration
from app.services.style_composer import drama_bible
from app.services.voice_assignment import list_unvoiced_characters


async def supervise_episode(db: Session, *, episode_id: int) -> dict:
    ep = db.get(Episode, episode_id)
    if ep is None or ep.deleted_at is not None:
        raise LookupError("分集不存在")

    drama = db.get(Drama, ep.drama_id)
    bible = drama_bible(drama)
    pipeline_blockers: list[str] = []
    if not bible.get("art_style_id") and not (drama.style if drama else None):
        pipeline_blockers.append("项目未设置画风（请到「项目」或「画风库」绑定项目画风）")
    if not (ep.script_content or "").strip():
        pipeline_blockers.append("本集尚无剧本")

    # 资产完备：角色/场景有图比例
    chars = list(db.scalars(select(Character).where(Character.drama_id == ep.drama_id, Character.deleted_at.is_(None))).all()) if drama else []
    scenes = list(db.scalars(select(Scene).where(Scene.drama_id == ep.drama_id, Scene.deleted_at.is_(None))).all()) if drama else []
    chars_with_img = sum(1 for c in chars if c.image_url or c.local_path)
    scenes_with_img = sum(1 for s in scenes if s.image_url or s.local_path)
    if chars and chars_with_img == 0:
        pipeline_blockers.append("角色资产尚无出图（分镜合成会缺人物参考）")
    if scenes and scenes_with_img == 0:
        pipeline_blockers.append("场景资产尚无出图（分镜合成会缺环境参考）")
    unvoiced = list_unvoiced_characters(db, ep.drama_id)
    if unvoiced:
        names = "、".join((c.name or "?") for c in unvoiced[:6])
        more = "…" if len(unvoiced) > 6 else ""
        pipeline_blockers.append(
            f"{len(unvoiced)} 个角色未绑定音色（{names}{more}），配音前请到「角色资产」一键绑定"
        )

    shots = list(
        db.scalars(
            select(Storyboard)
            .where(Storyboard.episode_id == episode_id, Storyboard.deleted_at.is_(None))
            .order_by(Storyboard.storyboard_number)
        ).all()
    )
    issues: list[dict] = []
    ready = 0
    for sb in shots:
        shot_issues: list[str] = []
        has_image = bool(sb.composed_image or sb.first_frame_image)
        has_video = bool(sb.video_url or sb.composed_video_url)
        has_tts = bool(sb.tts_audio_url)
        has_dialogue = bool((sb.dialogue or "").strip())
        cast_ids = list(db.scalars(select(StoryboardCharacter.character_id).where(StoryboardCharacter.storyboard_id == sb.id)).all())
        if cast_ids and not has_image:
            missing = []
            for cid in cast_ids:
                ch = db.get(Character, cid)
                if ch and not (ch.image_url or ch.local_path):
                    missing.append(ch.name or str(cid))
            if missing:
                shot_issues.append(f"出场角色未出图：{'、'.join(missing[:4])}，建议先角色资产出图")
        if not sb.scene_id and (sb.location or "").strip() and not has_image:
            shot_issues.append("未绑定场景资产，出图时可能缺少环境参考")

        if not has_image:
            shot_issues.append("缺少分镜图，请先出图")
        if not has_video:
            shot_issues.append("缺少视频，请用定稿出片 LTX-2.3 生成")
        if has_dialogue and not has_tts:
            shot_issues.append("有台词但无配音，请先 TTS")
        if has_dialogue:
            speaker = None
            if sb.speaking_character_id:
                speaker = db.get(Character, sb.speaking_character_id)
            if speaker is not None and not (speaker.voice_style or "").strip():
                shot_issues.append(f"说话角色「{speaker.name}」未绑定音色")
            elif speaker is None and (sb.dialogue or "").strip():
                # 无绑定说话人时尝试从台词前缀猜
                import re

                m = re.match(r"^\s*([^\s:：]{1,12})\s*[:：]", (sb.dialogue or "").splitlines()[0])
                if m and drama is not None:
                    name = m.group(1).strip()
                    ch = next((c for c in chars if c.name == name), None)
                    if ch is not None and not (ch.voice_style or "").strip():
                        shot_issues.append(f"台词说话人「{name}」未绑定音色")
        if has_dialogue and has_video and has_tts:
            # 检查是否用过 audio_driven
            last_v = db.scalars(
                select(VideoGeneration)
                .where(
                    VideoGeneration.storyboard_id == sb.id,
                    VideoGeneration.deleted_at.is_(None),
                )
                .order_by(VideoGeneration.id.desc())
            ).first()
            ref = (last_v.reference_mode or "") if last_v else ""
            if "audio_driven" not in ref and "LTX" not in ((last_v.model or "") if last_v else ""):
                shot_issues.append("对白镜建议用「定稿说话视频」做真口型（测试档无口型）")
        if sb.duration and sb.duration > 5:
            shot_issues.append(f"分镜时长 {sb.duration}s 超过 Comfy 单镜 5s，请拆镜")

        if shot_issues:
            issues.append(
                {
                    "storyboard_id": sb.id,
                    "number": sb.storyboard_number,
                    "title": sb.title,
                    "severity": "medium" if has_image else "severe",
                    "issues": shot_issues,
                    "actions": _actions_for(shot_issues),
                }
            )
        else:
            ready += 1

    severe = sum(1 for i in issues if i["severity"] == "severe")
    medium = sum(1 for i in issues if i["severity"] == "medium")
    grade = "A" if not issues else ("C" if severe else "B")
    summary = (
        f"共 {len(shots)} 镜，就绪 {ready}，问题镜 {len(issues)} "
        f"（严重 {severe} / 一般 {medium}）。等级 {grade}。"
    )
    return {
        "episode_id": episode_id,
        "grade": grade,
        "summary": summary,
        "total_shots": len(shots),
        "ready_shots": ready,
        "issue_count": len(issues),
        "issues": issues,
        "pipeline_blockers": pipeline_blockers,
        "asset_stats": {
            "characters": len(chars),
            "characters_with_image": chars_with_img,
            "scenes": len(scenes),
            "scenes_with_image": scenes_with_img,
            "has_style": bool(bible.get("art_style_id") or (drama.style if drama else None)),
            "has_script": bool((ep.script_content or "").strip()),
        },
        "pipeline_hint": {
            "final": "quality_mode=final → LTX-2.3，5s，定稿出片",
            "talking": "reference_mode=audio_driven + final，真口型",
        },
    }


def _actions_for(issues: list[str]) -> list[str]:
    actions = []
    for t in issues:
        if "分镜图" in t:
            actions.append("goto:storyboard_image")
        elif "测试档" in t or "视频" in t:
            actions.append("goto:timeline_video_test")
        elif "配音" in t or "TTS" in t:
            actions.append("goto:timeline_tts")
        elif "口型" in t:
            actions.append("goto:timeline_talking")
        elif "拆镜" in t:
            actions.append("goto:storyboard_split")
    return actions
