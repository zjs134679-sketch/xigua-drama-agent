# 西瓜短剧 · ComfyUI 工作流（仅 H3 Turbo）

旧工作流（Flux / Kontext / LTX / ImageToVideo / i2v）**已废弃**，归档在 `_legacy_unused/`，后端不再加载。

## 启用中的两套

| 文件 | 用途 |
|------|------|
| **`minimax-h3-t2i.api.json`** | 角色/场景文生图（Turbo 4 步，T2VA 微视频取首帧） |
| **`minimax-h3-r2v.api.json`** | 多参考成片视频（Turbo 4 步，Ref2VA + 提示词语音） |

均基于 `H3_Turbo_Stable_4V4A`：

- `LoraLoaderBypassModelOnly` + Turbo 4 步 EMA LoRA  
- `MiniMaxH3AudioConditioningT8`  
- `MiniMaxH3DualClockSamplerT8`（steps=4）  
- `MiniMaxH3AVDecodeT8`  

## 必须模型

```
models/unet/minimax_h3_fl2va_int8_convrot.safetensors   # 非 pruned
models/loras/minimax_h3_turbo_4步加速ema_comfyui.safetensors
models/text_encoders/qwen3vl_32b_minimax_h3_nvfp4_awq.safetensors
models/vae/minimax_h3_video_vae_fp16.safetensors
models/vae/minimax_h3_audio_vae_fp32.safetensors
```

自定义节点：`comfyui-minimax-h3-audio-T8`。

> Turbo LoRA **禁止** 与 `*_pruned_*` 基模同用（会 mat1×mat2 崩溃）。后端会自动纠正。

## 算力节点

```
workflow_t2i / workflow / workflow_refs  → minimax-h3-t2i.api.json
workflow_i2v / workflow_r2v / workflow_video → minimax-h3-r2v.api.json
unet_name → minimax_h3_fl2va_int8_convrot.safetensors
steps → 4
```
