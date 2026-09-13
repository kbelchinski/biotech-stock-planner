import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    // The browser only talks to the local backend; provider credentials never leave it.
    proxy: {
      "/api": "http://127.0.0.1:8000",
    },
  },
});
