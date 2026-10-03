import { defineConfig, loadEnv } from "vite";
import react from "@vitejs/plugin-react";

// 前端开发配置：/api 反向代理到本地 FastAPI 服务（默认 8766）。
export default defineConfig(({ mode }) => ({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      "/api": {
        target: loadEnv(mode, ".", "LIGHTTRAIL_").LIGHTTRAIL_API_TARGET ?? "http://127.0.0.1:8766",
        changeOrigin: true,
      },
      "/_AMapService": {
        target: loadEnv(mode, ".", "LIGHTTRAIL_").LIGHTTRAIL_API_TARGET ?? "http://127.0.0.1:8766",
        changeOrigin: true,
      },
    },
  },
}));
