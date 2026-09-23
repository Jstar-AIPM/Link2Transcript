import { defineConfig } from "vitest/config";
import react from "@vitejs/plugin-react";
import { fileURLToPath } from "node:url";

export default defineConfig({
  plugins: [react()],
  resolve: {
    alias: {
      "@": fileURLToPath(new URL("./src", import.meta.url)),
    },
  },
  test: {
    environment: "jsdom",
    // jsdom 需要一个具体来源才会启用 localStorage（否则是 opaque origin）
    environmentOptions: { jsdom: { url: "http://localhost:3100/" } },
    include: ["tests/**/*.test.ts", "tests/**/*.test.tsx"],
    globals: true,
  },
});
