// 西瓜短剧Agent 桌面壳（Tauri 2）。
// 开发期：前端 5173 + 后端 5678 由 启动.bat 分别拉起。
// 成品期（M6）：将 Python 后端 Nuitka 编译为二进制，作为 sidecar externalBin 由本壳拉起。
#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

fn main() {
    tauri::Builder::default()
        .run(tauri::generate_context!())
        .expect("运行 Tauri 应用出错");
}
