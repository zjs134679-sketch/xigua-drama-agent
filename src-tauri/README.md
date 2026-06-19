# src-tauri — 桌面壳（Tauri 2）

本机当前**未安装 Rust**，无法 `tauri dev/build`。补齐步骤：

1. 装 Rust：https://rustup.rs  （`rustup-init.exe`）
2. 装 Tauri CLI：`npm i -g @tauri-apps/cli`（或项目内 `npm i -D @tauri-apps/cli`）
3. 准备图标：把西瓜 Logo 存为 `app-icon.png`，运行 `tauri icon app-icon.png` 自动生成 `icons/`。
4. 开发：项目根 `tauri dev`（会按 `tauri.conf.json` 的 `beforeDevCommand` 拉起前端，并打开桌面窗口；后端用 `启动.bat` 或单独 `uvicorn` 起在 5678）。
5. 打包：`tauri build`。

> 成品化（M6）：把 Python 后端用 Nuitka 编成单文件 exe，放入 `externalBin` 作为 sidecar，由 `main.rs` 在 `setup` 中拉起，实现"双击即用、无需用户装 Python"。
