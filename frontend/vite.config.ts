import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// 前端 5173；/api/* 反代到后端 5678（去掉 /api 前缀）
export default defineConfig({
  plugins: [react()],
  server: {
    host: true,
    port: 5173,
    proxy: {
      "/api": {
        target: "http://127.0.0.1:5678",
        changeOrigin: true,
        rewrite: (p) => p.replace(/^\/api/, ""),
      },
      "/oss": {
        target: "http://127.0.0.1:5678",
        changeOrigin: true,
      },
    },
  },
});
