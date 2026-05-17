import { vitePreprocess } from "@sveltejs/vite-plugin-svelte";

export default {
  preprocess: vitePreprocess(),
  // Svelte 5 — keep compiler options minimal; runes are enabled by default
  // for .svelte files in v5.
};
