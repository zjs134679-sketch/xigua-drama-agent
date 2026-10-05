# 第三方声明

## 来源

本技能（`xg_h3_video_prompt`）的提示词方法论、参考文件和校验脚本，
基于开源项目 [minimax-h3-video-prompt](https://github.com/penposs/minimax-h3-video-prompt)（MIT 协议）改编。

原作者：penposs
原仓库：https://github.com/penposs/minimax-h3-video-prompt

## 改编内容

- SKILL.md 已针对西瓜短剧场景进行适配（3–5 秒单镜、短剧叙事结构、情绪递进等）
- reference/ 文件在保持技术准确性的前提下进行了精简和中文优化
- scripts/validate_h3_prompt.py 适配为与西瓜项目一致的 Python 环境

## 商标声明

MiniMax、H3 是 MiniMax 公司的商标。
本项目（西瓜短剧智能体）与 MiniMax 公司无任何关联、 endorsement 或 sponsorship 关系。
本技能仅为 MiniMax H3 API 的提示词工程指南，H3 API 参数规则来自其公开文档。

## 许可证

本技能目录下的内容（SKILL.md、reference/、scripts/）沿用原项目的 MIT 许可证。
详见同目录 LICENSE 文件。
