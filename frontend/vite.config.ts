import { defineConfig } from "vite";
import { svelte } from "@sveltejs/vite-plugin-svelte";

// Vite 5 + @sveltejs/vite-plugin-svelte 4 = the supported pairing for Svelte 5.
// The bundle output is written into the Python package's static/ folder so
// `pip install clicketysplit` ships the same assets without a Node step.
export default defineConfig({
  plugins: [svelte()],
  build: {
    outDir: "../src/clicketysplit/server/static",
    emptyOutDir: true,
  },
  server: {
    proxy: {
      "/api": {
        target: "http://127.0.0.1:5000",
        changeOrigin: true,
      },
    },
  },
});
