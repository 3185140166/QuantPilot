import { defineConfig } from "vite";
import vue from "@vitejs/plugin-vue";

export default defineConfig({
  plugins: [vue()],
  optimizeDeps: {
    include: [
      "element-plus/es/components/alert/index.mjs",
      "element-plus/es/components/button/index.mjs",
      "element-plus/es/components/checkbox/index.mjs",
      "element-plus/es/components/collapse/index.mjs",
      "element-plus/es/components/config-provider/index.mjs",
      "element-plus/es/components/date-picker/index.mjs",
      "element-plus/es/components/descriptions/index.mjs",
      "element-plus/es/components/dialog/index.mjs",
      "element-plus/es/components/empty/index.mjs",
      "element-plus/es/components/form/index.mjs",
      "element-plus/es/components/icon/index.mjs",
      "element-plus/es/components/input-number/index.mjs",
      "element-plus/es/components/input/index.mjs",
      "element-plus/es/components/message-box/index.mjs",
      "element-plus/es/components/message/index.mjs",
      "element-plus/es/components/pagination/index.mjs",
      "element-plus/es/components/progress/index.mjs",
      "element-plus/es/components/radio/index.mjs",
      "element-plus/es/components/select/index.mjs",
      "element-plus/es/components/switch/index.mjs",
      "element-plus/es/components/table/index.mjs",
      "element-plus/es/components/tag/index.mjs",
      "element-plus/es/components/tooltip/index.mjs",
    ],
  },
  server: {
    port: 5173,
    strictPort: true,
    proxy: {
      "/api": {
        target: process.env.PLATFORM_API_TARGET ?? "http://127.0.0.1:8000",
        configure(proxy) {
          proxy.on("error", (_error, _request, response) => {
            if ("writeHead" in response && !response.headersSent) {
              response.writeHead(503, {
                "Content-Type": "application/json",
                "Retry-After": "1",
              });
              response.end(
                JSON.stringify({ detail: "后台服务正在重新连接，请稍候" }),
              );
            }
          });
        },
      },
    },
  },
  build: {
    rollupOptions: {
      output: {
        manualChunks(id) {
          if (
            id.includes("/node_modules/echarts/") ||
            id.includes("/node_modules/zrender/")
          )
            return "charts";
          if (id.includes("/node_modules/element-plus/")) return "ui";
        },
      },
    },
  },
});
