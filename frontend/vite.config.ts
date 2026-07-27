import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

import react from "@vitejs/plugin-react";
import { VitePWA } from "vite-plugin-pwa";
import type { Plugin } from "vite";
import { defineConfig } from "vitest/config";

const localeDirectory = fileURLToPath(new URL("../lang", import.meta.url));

function localeXmlPlugin(): Plugin {
  const publicId = "virtual:coop-locales";
  const resolvedId = `\0${publicId}`;
  return {
    name: "cooperative-locale-xml",
    resolveId(id) {
      return id === publicId ? resolvedId : null;
    },
    load(id) {
      if (id !== resolvedId) return null;
      const files = fs.readdirSync(localeDirectory)
        .filter((name) => name.endsWith(".xml"))
        .sort();
      const locales = Object.fromEntries(files.map((name) => {
        const filename = path.join(localeDirectory, name);
        this.addWatchFile(filename);
        return [name, fs.readFileSync(filename, "utf8")];
      }));
      return `export default ${JSON.stringify(locales)};`;
    },
  };
}

export default defineConfig({
  plugins: [
    localeXmlPlugin(),
    react(),
    VitePWA({
      registerType: "autoUpdate",
      injectRegister: "auto",
      manifest: {
        name: "Cooperative Clearing",
        short_name: "Clearing",
        description: "Local cooperative clearing node",
        theme_color: "#16352d",
        background_color: "#f4f6f5",
        display: "standalone",
        start_url: "/",
        icons: [
          {
            src: "/mark.svg",
            sizes: "any",
            type: "image/svg+xml",
            purpose: "any",
          },
        ],
      },
      workbox: {
        clientsClaim: true,
        cleanupOutdatedCaches: true,
        skipWaiting: true,
        navigateFallback: "/index.html",
        runtimeCaching: [
          { urlPattern: /\/api\//, handler: "NetworkOnly" },
          { urlPattern: /\/health\//, handler: "NetworkOnly" },
        ],
      },
    }),
  ],
  server: {
    port: 5173,
    proxy: {
      "/api": "http://localhost:8000",
      "/health": "http://localhost:8000",
    },
  },
  test: {
    environment: "jsdom",
    setupFiles: ["./src/test/setup.ts"],
    testTimeout: 15_000,
    coverage: {
      provider: "v8",
      reporter: ["text", "json-summary"],
      thresholds: {
        lines: 75,
        functions: 75,
        branches: 70,
        statements: 75,
      },
    },
  },
});