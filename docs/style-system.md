# 西瓜风格体系说明

> 西瓜短剧 Agent 原创风格体系。

## 推荐生产顺序（全链路衔接）

```
项目画风 → 剧本/事件改编 → 提取资产 → 角色出图 → 场景出图(无人) → 分镜拆解 → 分镜出图(多参考) → 审核 → 成片
```

顶栏 **流水线** 会显示本集缺哪一步，并提供「下一步」跳转。

## 用户该改哪里（唯一真相）

| 入口 | 作用 | 是否决定出图 |
|------|------|----------------|
| **项目 → 项目画风** / 画风库「设为项目画风」 | 写入 `style_bible.art_style_id` | **是，全局默认** |
| 画风库「保存编辑」 | 只改预设文字（后缀/手册） | 否（除非该预设已被项目绑定） |
| 角色/场景/道具下拉 | 默认「跟随项目」；选手动项=仅本次覆盖 | 临时覆盖时生效 |

未选手动画风时，后端按项目 `style_bible` 解析；不再误用「库内排序第一项」冒充默认。

## 结构

| 层 | 名称 | 位置 |
|----|------|------|
| L0 | 平台硬约束 | `data/skills/contracts/platform.json` |
| L1 | 项目风格圣经 | `dramas.style_bible` JSON |
| L2 | 阶段注入 | `StyleComposer` → 编剧/分镜/定装/分镜图/视频/审核 |
| L3 | 机读契约 | `StyleContract` |

## 风格圣经字段

- `art_style_id` / `visual_pack` / `visual_name`：画风
- `narrative_tag`：故事类型（`story_types/*`）
- `pacing_profile`：节奏取向（`pacing_profiles/*`）
- `aspect`：`9:16` 或 `16:9`

## 兼容矩阵

`data/skills/contracts/style_matrix.json`：节奏 × 画风族 → recommend / allow / caution / **forbid**。  
禁止组合在建项与更新风格圣经时返回 HTTP 400。

## API

- `GET /projects/style-options`
- `GET|PUT /projects/{id}/style-bible`
- 创建项目 `POST /projects` 可带 `art_style_id`、`pacing_profile`、`narrative_tag`、`aspect`

## 阶段差异（摘要）

| 阶段 | 要点 |
|------|------|
| 定装 identity | 中性光、锁五官服装 |
| 分镜图 shot_image | 允许情绪光，不套定装平光句 |
| 视频 video | 风格标签 + 时长钳制 |
| 审核 audit | 锚词缺失 / 禁用词 |

## 老项目

无 `style_bible` 时按 `style` / `genre` 降级组装，行为不低于旧版。
