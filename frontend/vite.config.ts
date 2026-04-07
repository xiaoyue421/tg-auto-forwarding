import vue from "@vitejs/plugin-vue";
import path from "path";
import { fileURLToPath } from "url";
import { defineConfig } from "vite";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const staticOut = path.resolve(__dirname, "../src/tg_forwarder/web/static");

export default defineConfig({
  plugins: [vue()],
  base: "./",
  build: {
    outDir: staticOut,
    emptyOutDir: true,
  },
  server: {
    port: 5173,
    proxy: {
      "/api": { target: "http://127.0.0.1:8080", changeOrigin: true },
    },
  },
});
