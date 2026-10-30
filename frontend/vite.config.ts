import { realpathSync } from "node:fs";
import { defineConfig, searchForWorkspaceRoot } from "vite";
import react from "@vitejs/plugin-react";
// AXIOM_DEV_API points the dev server at another backend, for looking at a change against real data.
const backend = process.env.AXIOM_DEV_API || "http://127.0.0.1:8080";
export default defineConfig({
  plugins: [react()],
  build: { outDir: "../jobpilot/static", emptyOutDir: true },
  server: {
    // A git worktree links node_modules from the main checkout: without this the dev server refuses to serve the fonts.
    fs: { allow: [searchForWorkspaceRoot(process.cwd()), realpathSync("node_modules")] },
    proxy: {
      "/api": backend,
      "/healthz": backend,
    },
  },
});
