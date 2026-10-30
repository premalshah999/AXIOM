import { realpathSync } from "node:fs";
import { defineConfig, searchForWorkspaceRoot } from "vite";
import react from "@vitejs/plugin-react";
export default defineConfig({
  plugins: [react()],
  build: { outDir: "../jobpilot/static", emptyOutDir: true },
  server: {
    // A git worktree links node_modules from the main checkout: without this the dev server refuses to serve the fonts.
    fs: { allow: [searchForWorkspaceRoot(process.cwd()), realpathSync("node_modules")] },
    proxy: {
      "/api": "http://127.0.0.1:8080",
      "/healthz": "http://127.0.0.1:8080",
    },
  },
});
