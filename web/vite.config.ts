import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// VITE_BASE is "/<repo>/" on GitHub Pages.
export default defineConfig({
  base: process.env.VITE_BASE || "/",
  plugins: [react()],
  worker: { format: "es" },
});
