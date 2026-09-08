import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// 前端开发配置：/api 反向代理到本地 FastAPI 服务（默认 8765，与 E7-0 联调端口一致）
export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      "/api": {
        target: "http://127.0.0.1:8765",
        changeOrigin: true,
      },
    },
  },
});
