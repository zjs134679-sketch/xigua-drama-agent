# -*- coding: utf-8 -*-
"""生成西瓜短剧原创画风技能包（data/skills/art_styles）。

结构（西瓜自有，非第三方布局）：
  <skill_key>/
    SKILL.md
    README.md
    constraint.md
    prompts/{character,character_var,scene,scene_var,prop,prop_var,shot_video}.md
    direction/{planning,storyboard,storyboard_table}.md
"""
from __future__ import annotations

import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BASE = ROOT / "data" / "skills" / "art_styles"

# ---------------------------------------------------------------------------
# 画风定义：全新 skill_key，中文产品名保留业务可读性
# ---------------------------------------------------------------------------
STYLES: list[dict] = [
    {
        "key": "xg_live_metro",
        "display_name": "真人都市写实",
        "description": "当代中国城市实拍质感，自然光与生活痕迹",
        "tags": ["真人", "都市", "写实", "短剧", "主推"],
        "sort_order": 0,
        "prompt_suffix": "真人写实,当代中国都市,电影剧照,自然光,皮肤纹理,生活痕迹,无二次元,无CG感",
        "genre": "live_urban",
        "one_liner": "摄影机拍下的当代中国都市生活",
        "anchors": "真人写实摄影, 电影剧照, 当代中国都市, 自然光逻辑清晰, 皮肤纹理真实, 生活现场感",
        "forbid": "二次元/插画/3D渲染感；古装仙侠；过度磨皮蜡像脸；水印文字UI",
        "video_tags": "真人都市短剧,自然光,纪实手持,生活痕迹",
    },
    {
        "key": "xg_live_period",
        "display_name": "真人古风写实",
        "description": "古装影视写实质感，织物与肌肤纹理清晰",
        "tags": ["真人", "古风", "写实", "历史"],
        "sort_order": 1,
        "prompt_suffix": "真人古装写实,影视剧照,织物刺绣细节,自然肤质,冷底暖点,无现代道具,无二次元",
        "genre": "live_period",
        "one_liner": "院线古装剧的实拍影像质感",
        "anchors": "真人古装写实, 影视剧照, 汉服织物细节, 自然肤质, 冷色底暖高光",
        "forbid": "现代服装道具；霓虹赛博；卡通渲染；假塑料盔甲感",
        "video_tags": "真人古装,影视质感,织物细节,冷暖对比",
    },
    {
        "key": "xg_cel_guofeng",
        "display_name": "国风二次元新国潮",
        "description": "赛璐璐国风动画，东方结构与新潮配色",
        "tags": ["二次元", "国风", "动画", "新国潮"],
        "sort_order": 2,
        "prompt_suffix": "国风二次元,赛璐璐平涂,新国潮,清晰线稿,东方构图,无写实照片感",
        "genre": "cel_guofeng",
        "one_liner": "清晰线稿的国风赛璐璐动画",
        "anchors": "国风二次元, 赛璐璐上色, 清晰线稿, 新国潮配色, 东方意境构图",
        "forbid": "照片写实；厚涂油画；西方奇幻铠甲乱入",
        "video_tags": "国风动画,赛璐璐,清晰线稿,新国潮",
    },
    {
        "key": "xg_cel_retro90",
        "display_name": "90年代日系动画风",
        "description": "怀旧平涂动画，柔和暖调与电影感层次",
        "tags": ["二次元", "怀旧", "动画", "日系"],
        "sort_order": 3,
        "prompt_suffix": "九十年代动画风,手绘平涂,柔和暖调,清晰线条,怀旧电影感,无3D渲染",
        "genre": "cel_retro",
        "one_liner": "暖调怀旧的手绘平涂动画",
        "anchors": "九十年代动画美学, 手绘平涂, 柔和暖调, 清晰流畅线条",
        "forbid": "超写实皮肤；赛博霓虹过载；黏土/3D建模感",
        "video_tags": "怀旧动画,平涂,暖调,手绘线条",
    },
    {
        "key": "xg_cel_romance",
        "display_name": "都市甜宠插画",
        "description": "成熟都市情感向插画，柔光与时尚穿搭",
        "tags": ["插画", "都市", "甜宠", "情感"],
        "sort_order": 4,
        "prompt_suffix": "都市情感插画,柔光美颜适度,时尚穿搭,干净背景,成熟气质,非儿童向",
        "genre": "illust_romance",
        "one_liner": "柔光时尚的都市情感插画",
        "anchors": "都市情感插画, 柔和轮廓光, 时尚通勤穿搭, 干净画面层次",
        "forbid": "恐怖血腥；过度二次元幼态；脏乱暗黑赛博",
        "video_tags": "都市插画,柔光,时尚,情感短剧",
    },
    {
        "key": "xg_flat_pop",
        "display_name": "扁平矢量风",
        "description": "几何色块扁平设计，强图形语言",
        "tags": ["扁平", "矢量", "设计", "轻量"],
        "sort_order": 5,
        "prompt_suffix": "扁平矢量设计,几何色块,干净轮廓,有限配色,无渐变厚涂,无照片纹理",
        "genre": "flat_vector",
        "one_liner": "色块分明的扁平矢量图形",
        "anchors": "扁平矢量, 几何色块, 有限配色, 干净轮廓, 无照片噪点",
        "forbid": "写实皮肤毛孔；复杂PBR材质；手绘素描脏线",
        "video_tags": "扁平设计,矢量色块,干净图形",
    },
    {
        "key": "xg_render_anime3d",
        "display_name": "三维二次元渲染",
        "description": "干净的三维动画渲染，卡通材质与柔和打光",
        "tags": ["3D", "二次元", "渲染", "动画"],
        "sort_order": 6,
        "prompt_suffix": "三维动画渲染,卡通材质,柔和三点光,干净拓扑感,非写实摄影,非黏土",
        "genre": "render_anime",
        "one_liner": "干净的三维卡通动画渲染",
        "anchors": "三维二次元渲染, 卡通着色, 柔和灯光, 干净材质分区",
        "forbid": "真人照片；低多边形故障感；过度金属反光",
        "video_tags": "三维动画,卡通渲染,柔和灯光",
    },
    {
        "key": "xg_render_cntrad",
        "display_name": "三维国风传统",
        "description": "三维国风场景与服饰，木质石材与织物层次",
        "tags": ["3D", "国风", "传统", "渲染"],
        "sort_order": 7,
        "prompt_suffix": "三维国风,传统建筑服饰,木质石材织物,东方光影,非现代都市,非赛博",
        "genre": "render_cn",
        "one_liner": "有材质层次的三维国风影像",
        "anchors": "三维国风, 传统建筑, 织物木石材质, 东方光影层次",
        "forbid": "现代玻璃幕墙；霓虹赛博；欧美中世纪乱入",
        "video_tags": "三维国风,传统材质,东方光影",
    },
    {
        "key": "xg_clay_stop",
        "display_name": "粘土定格风",
        "description": "手工粘土定格动画质感，可见指纹与材料边界",
        "tags": ["粘土", "定格", "手工", "可爱"],
        "sort_order": 8,
        "prompt_suffix": "粘土定格动画,手工材质,指纹痕迹,柔和棚拍光,非写实皮肤,非赛璐璐",
        "genre": "clay",
        "one_liner": "带手工痕迹的粘土定格画面",
        "anchors": "粘土定格, 手工捏塑痕迹, 柔和棚灯, 材质边界清晰",
        "forbid": "真人皮肤；赛璐璐线稿；超写实金属机械",
        "video_tags": "粘土定格,手工材质,柔和棚光",
    },
    {
        "key": "xg_cyber_east",
        "display_name": "东方赛博风",
        "description": "东方建筑结构叠加霓虹科技元素",
        "tags": ["赛博", "国风", "科技", "霓虹"],
        "sort_order": 9,
        "prompt_suffix": "东方赛博,霓虹夜色,传统结构加科技装置,冷暖霓虹对比,非纯古装写实",
        "genre": "cyber_east",
        "one_liner": "东方骨架上的霓虹科技夜色",
        "anchors": "东方赛博, 霓虹夜色, 传统飞檐与全息装置, 冷暖对比",
        "forbid": "纯写实无科技；乡村田园；儿童糖果色扁平",
        "video_tags": "东方赛博,霓虹,科技国风",
    },
    {
        "key": "xg_cine_real",
        "display_name": "写实电影感",
        "description": "通用电影剧照写实，横竖屏短剧通用兜底",
        "tags": ["电影", "写实", "通用", "兜底"],
        "sort_order": 10,
        "prompt_suffix": "电影剧照写实,统一色调,景深层次,可信光影,无水印文字,无夸张滤镜",
        "genre": "cine",
        "one_liner": "通用电影剧照写实兜底风格",
        "anchors": "电影剧照, 写实光影, 统一色调, 景深层次, 无水印",
        "forbid": "杂乱多风格混搭；夸张美颜失效写实；UI边框水印",
        "video_tags": "电影感,写实,统一色调,景深",
    },
]


def _constraint_md(s: dict) -> str:
    return f"""# 西瓜画风约束 · {s['display_name']}

> 西瓜短剧 Agent 原创画风手册。生成提示词时必须遵守本节，只输出提示词正文，不附加解释。

## 1. 视觉定位
- **一句话**：{s['one_liner']}
- **适用**：国内竖屏/横屏短剧分镜与角色定装
- **类型码**：`{s['genre']}`

## 2. 出图必带词
将下列中文锚词写入正向提示（可微调语序，不可整段删除）：

`{s['anchors']}`

## 3. 镜头与构图
| 项目 | 要求 |
|---|---|
| 主体清晰度 | 角色面部与关键道具必须可读 |
| 构图 | 优先中近景叙事，避免无意义大远景空镜堆砌 |
| 景深 | 允许浅景深突出主体，背景勿抢戏 |
| 画幅 | 竖屏 9:16 或横屏 16:9，与项目设定一致 |

## 4. 光影与材质
- 光源可解释（窗光、路灯、棚灯、天光等），冷暖关系自洽
- 材质符合本画风（见「视觉定位」），禁止跨风格材质乱入
- 服装与场景有「被使用过」的可信细节，拒绝空壳样板间（除非剧情需要）

## 5. 角色一致性
- 同一角色多镜头保持发色、五官比例、服装主色一致
- 年龄感与剧本设定一致，禁止无故幼态化或老年化
- 双手五指结构正常，禁止明显畸形

## 6. 禁用清单
{s['forbid']}
- 任何可读水印、字幕条、手机 UI、二维码
- 血腥过度暴露（除非合规审查通过且剧情必需）

## 7. 情绪光效速查（择一写入）
| 情绪 | 光感关键词 |
|---|---|
| 平静日常 | 均匀柔光、低对比 |
| 暧昧靠近 | 侧暖光、柔边 |
| 冲突对峙 | 硬侧光、冷暖对切 |
| 孤独沉思 | 低照度、轮廓光 |
| 高潮释放 | 高对比、强调主光 |

## 8. 输出纪律
- 仅输出可用于模型的提示词正文
- 优先中文描述；英文仅作补充关键词
- 不复述本手册标题与条款编号
"""


def _skill_md(s: dict) -> str:
    tags = ", ".join(s["tags"])
    return f"""---
name: {s['key']}
display_name: {s['display_name']}
description: {s['description']}
tags: [{tags}]
prompt_suffix: {s['prompt_suffix']}
sort_order: {s['sort_order']}
---

# {s['display_name']}

西瓜短剧原创画风技能。完整约束见 `constraint.md`，出图模板见 `prompts/`，分镜方法见 `direction/`。

## 快速摘要
- {s['one_liner']}
- 锚词：{s['anchors']}
- 禁用：{s['forbid']}

## 使用方式
1. 项目绑定本画风后，系统自动注入约束摘要与视频风格标签
2. 角色/场景/道具出图读取 `prompts/` 对应模板
3. 分镜拆解读取 `direction/` 导演方法
"""


def _readme(s: dict) -> str:
    return f"""# {s['display_name']}（{s['key']}）

{s['description']}

## 目录
- `SKILL.md` — 元数据与摘要
- `constraint.md` — 全局视觉约束（出图必守）
- `prompts/` — 角色/场景/道具/镜头视频模板
- `direction/` — 导演规划与分镜表方法

## 维护
本包为西瓜短剧 Agent 原创内容，可按项目需要在设置页在线编辑。
"""


def _prompt_character(s: dict) -> str:
    return f"""# 角色定装提示词模板 · {s['display_name']}

## 目标
生成可用于短剧定装的角色图提示词，保证后续分镜可复用同一身份特征。

## 输出结构（按序拼接）
1. **画风锚词**：`{s['anchors']}`
2. **身份**：年龄段、性别气质、职业/身份标签（来自资产描述）
3. **五官与发型**：发型长度/刘海/发色；眉眼口鼻关键特征 2–4 条
4. **服装**：上装+下装+鞋；主色与辅色；材质（棉麻/西装料/丝绸等）
5. **体态站姿**：站姿或半身构图说明
6. **光线**：符合本画风的一条光配方
7. **背景**：简洁纯色/虚化环境，勿抢主体
8. **质量约束**：构图干净、四肢结构正常、无文字水印

## 禁止
{s['forbid']}

## 示例骨架
```
{s['anchors']}，[身份]，[发型五官]，[服装主色材质]，[站姿半身]，[光线]，简洁背景，无文字无水印
```
"""


def _prompt_character_var(s: dict) -> str:
    return f"""# 角色衍生提示词模板 · {s['display_name']}

## 目标
在**不改变身份锚点**的前提下，生成服装/状态/表情变体。

## 锁定（不可改）
- 五官比例、发色、年龄感、基础体型
- 画风：{s['one_liner']}

## 可改
- 服装套装、表情、动作、季节外套、轻伤/疲惫等状态
- 场景氛围（仍须符合画风）

## 输出
先复述锁定特征 1 句，再写变体服装与状态，最后附锚词：`{s['anchors']}`
"""


def _prompt_scene(s: dict) -> str:
    return f"""# 场景提示词模板 · {s['display_name']}

## 目标
生成可承载叙事的环境空镜/建立镜头提示词。

## 输出结构
1. 画风锚词：`{s['anchors']}`
2. 地点类型（室内/室外/交通工具等）
3. 空间层次：前景-中景-背景各 1 个物件
4. 时间与天气
5. 主光源与色温
6. 生活痕迹或风格化装饰（符合画风）
7. 无人物或仅极远虚影（除非资产要求有人）

## 禁止
{s['forbid']}
"""


def _prompt_scene_var(s: dict) -> str:
    return f"""# 场景衍生提示词模板 · {s['display_name']}

同一场景的时间/天气/灯光变体。保持空间结构与标志性陈设不变。
锚词：`{s['anchors']}`
可改：晨昏、晴雨、开灯关灯、人流密度（仍避免抢戏人物特写）。
"""


def _prompt_prop(s: dict) -> str:
    return f"""# 道具提示词模板 · {s['display_name']}

## 目标
生成可识别、可复用的关键道具特写。

## 输出结构
1. 锚词：`{s['anchors']}`
2. 道具名称与功能
3. 材质、颜色、新旧程度、磨损痕迹
4. 尺度参考（相对手掌/桌面）
5. 干净背景或简易台面
6. 无文字品牌 logo（除非剧情必需且已合规）
"""


def _prompt_prop_var(s: dict) -> str:
    return f"""# 道具衍生提示词模板 · {s['display_name']}

保持道具识别特征，可变：破损、打开/闭合、沾污、特殊光下的反光。
锚词：`{s['anchors']}`
"""


def _prompt_shot_video(s: dict) -> str:
    return f"""# 分镜视频风格标签 · {s['display_name']}

| 字段 | 内容 |
|---|---|
| 风格标签 | `{s['video_tags']}` |
| 画风一句 | {s['one_liner']} |
| 运镜建议 | 叙事优先：推近表情、横移跟随、固定中景对话 |
| 时长建议 | 单镜 3–5 秒，动作完整可停 |

生成图生视频提示时，将「风格标签」与镜头动作、情绪一并写入。
"""


def _dir_planning(s: dict) -> str:
    return f"""# 导演规划方法 · {s['display_name']}

## 职责
把单集剧情拆成可执行的场次与情绪曲线，并绑定画风 **{s['display_name']}**。

## 步骤
1. 标出本集起承转合四个情绪锚点
2. 每场：地点 / 在场人物 / 冲突目标 / 结束状态
3. 为每场指定 1 个主视觉钩子（物件、服装变化或空间反差）
4. 检查是否违反画风禁用：{s['forbid']}

## 输出格式
Markdown 列表：场次编号、摘要、情绪、主视觉钩子、预估镜头数。
"""


def _dir_storyboard(s: dict) -> str:
    return f"""# 分镜拆解方法 · {s['display_name']}

## 原则
- 一镜一事：每镜只推进一个信息点（表情/动作/反应/环境）
- 对话镜：优先过肩与正反打，插入物写支撑潜台词
- 动作镜：起幅-动作-落幅清楚，方便 5 秒级出片
- 画风贯穿：{s['one_liner']}

## 镜头字段
镜号 | 景别 | 运镜 | 画面内容 | 对白/旁白 | 情绪 | 参考资产

## 景别建议占比（可按类型微调）
- 近景/特写 40–55%（表情与道具）
- 中景 25–35%（互动）
- 全景/建立 10–20%（空间）
"""


def _dir_storyboard_table(s: dict) -> str:
    return f"""# 分镜表样式 · {s['display_name']}

导出或展示分镜表时使用下列列：

| 镜号 | 时长 | 景别 | 运镜 | 画面描述 | 对白 | 音效 | 关联角色 | 关联场景 | 备注 |
|---|---|---|---|---|---|---|---|---|---|

- 画面描述必须可直接改写成出图提示，并隐含画风：`{s['video_tags']}`
- 备注栏写：口型需求 / 安全区 / 合规注意
"""


def write_style(s: dict) -> None:
    root = BASE / s["key"]
    if root.exists():
        shutil.rmtree(root)
    (root / "prompts").mkdir(parents=True)
    (root / "direction").mkdir(parents=True)

    files = {
        "SKILL.md": _skill_md(s),
        "README.md": _readme(s),
        "constraint.md": _constraint_md(s),
        "prompts/character.md": _prompt_character(s),
        "prompts/character_var.md": _prompt_character_var(s),
        "prompts/scene.md": _prompt_scene(s),
        "prompts/scene_var.md": _prompt_scene_var(s),
        "prompts/prop.md": _prompt_prop(s),
        "prompts/prop_var.md": _prompt_prop_var(s),
        "prompts/shot_video.md": _prompt_shot_video(s),
        "direction/planning.md": _dir_planning(s),
        "direction/storyboard.md": _dir_storyboard(s),
        "direction/storyboard_table.md": _dir_storyboard_table(s),
    }
    for rel, text in files.items():
        path = root / rel
        path.write_text(text.lstrip() if text.startswith("\n") else text, encoding="utf-8")
        if not path.read_text(encoding="utf-8").endswith("\n"):
            path.write_text(path.read_text(encoding="utf-8") + "\n", encoding="utf-8")


def main() -> None:
    BASE.mkdir(parents=True, exist_ok=True)
    # 删除全部旧包（含历史同源命名目录）
    if BASE.is_dir():
        for child in list(BASE.iterdir()):
            if child.is_dir():
                shutil.rmtree(child)
            elif child.is_file():
                child.unlink()

    for s in STYLES:
        write_style(s)
        print(f"wrote {s['key']} ({s['display_name']})")
    print(f"done: {len(STYLES)} packs -> {BASE}")


if __name__ == "__main__":
    main()
