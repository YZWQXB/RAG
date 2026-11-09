import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";

export default defineConfig({
  plugins: [react(), tailwindcss()],
  server: {
    port: 5173,
    proxy: {
      "/api": {
        // 用 127.0.0.1 而不是 localhost：Node 解析 localhost 可能优先 ::1，
        // 而 API 只监听 127.0.0.1，会导致代理连接被拒。
        target: "http://127.0.0.1:8000",
        changeOrigin: true,
      },
    },
  },
});
