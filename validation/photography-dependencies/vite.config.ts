import { defineConfig } from "vitest/config";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import react from "@vitejs/plugin-react";
import { VitePWA } from "vite-plugin-pwa";

const projectRoot = dirname(fileURLToPath(import.meta.url));
const animalIslandRuntime = resolve(projectRoot, "src/vendor/animal-island-ui-runtime.mjs");

export default defineConfig({
  resolve: {
    alias: [
      { find: /^animal-island-ui$/, replacement: animalIslandRuntime },
      { find: /^fs$/, replacement: resolve(projectRoot, "src/lib/empty-node-module.ts") },
    ],
  },
  build: {
    manifest: true,
    assetsInlineLimit: 0,
    chunkSizeWarningLimit: 700,
    rollupOptions: {
      output: {
        codeSplitting: {
          groups: [
            {
              name: "shared-navigation",
              test: /src[\\/](?:routing[\\/]RoutePreloadProvider\.tsx|components[\\/]shared[\\/]PrefetchLink\.tsx|lib[\\/]view-transition\.ts)$/,
              priority: 110,
            },
            {
              name: "react-vendor",
              test: /node_modules[\\/](?:react|react-dom|scheduler)[\\/]/,
              priority: 100,
            },
            {
              name: "i18n-vendor",
              test: /node_modules[\\/](?:i18next|react-i18next)[\\/]/,
              priority: 90,
            },
            {
              name: "router-vendor",
              test: /node_modules[\\/]react-router[\\/]/,
              priority: 80,
            },
            {
              name: "icon-vendor",
              test: /node_modules[\\/]lucide-react[\\/]/,
              priority: 70,
            },
            {
              name: "immersive-vendor",
              test: /node_modules[\\/]three[\\/]/,
              priority: 60,
            },
            {
              name: "face-api-vendor",
              test: /node_modules[\\/]face-api\.js[\\/]/,
              priority: 60,
            },
            {
              name: "motion-vendor",
              test: /node_modules[\\/]framer-motion[\\/]/,
              priority: 60,
            },
            {
              name: "lightbox-vendor",
              test: /node_modules[\\/]photoswipe[\\/]/,
              priority: 60,
            },
            {
              name: "animation-vendor",
              test: /node_modules[\\/]gsap[\\/]/,
              priority: 60,
            },
            {
              name: "swiper-vendor",
              test: /node_modules[\\/]swiper[\\/]/,
              priority: 60,
            },
            {
              name: "map-vendor",
              test: /node_modules[\\/](?:leaflet|react-leaflet)[\\/]/,
              priority: 50,
            },
          ],
        },
      },
    },
  },
  plugins: [
    react(),
    VitePWA({
      registerType: "prompt",
      injectRegister: "script-defer",
      manifest: false,
      includeAssets: [
        "manifest.webmanifest",
        "wechat-share.jpg",
        "icons/pwa-icon.svg",
        "icons/pwa-icon-192.png",
        "icons/pwa-icon-512.png",
        "icons/pwa-maskable-512.png",
      ],
      workbox: {
        skipWaiting: true, // 热推#2：旧会话浏览器面板里的旧版课堂标签强制升级（这次保持到用户确认静音）
        clientsClaim: true,
        cleanupOutdatedCaches: true,
        navigateFallback: "index.html",
        maximumFileSizeToCacheInBytes: 4 * 1024 * 1024,
        globPatterns: ["**/*.{js,css,html}"],
        globIgnores: [
          "**/images/gallery/**/*",
          "**/images/concept-premiere/**/*",
          "**/images/optical-archive/**/*",
          "**/images/visual-os-v5/**/*",
          "**/images/visual-os-v6/**/*",
          "**/images/visual-os-v7/**/*",
          "**/images/visual-os-v8/**/*",
          "**/immersive-vendor-*.js",
          "**/face-api-vendor-*.js",
          "**/map-vendor-*.js",
          "**/animation-vendor-*.js",
          "**/swiper-vendor-*.js",
          "**/lightbox-vendor-*",
          "**/en-*.js",
          "**/ja-*.js",
          "**/ko-*.js",
          // 注意：zh-CN（站点主语言 locale chunk，约 69KB）刻意不排除 ——
          // 它必须进 precache，否则全新安装后离线首启时框架文案回退英文
        ],
        runtimeCaching: [
          {
            // law 数据分块（loader 按需 fetch 的哈希 JSON 资源）：
            // Vite 产物均带内容哈希、内容不可变 → CacheFirst；
            // 进过课即离线可复学（law:offline 实测覆盖）
            urlPattern: ({ url }) => url.pathname.startsWith("/assets/") && url.pathname.endsWith(".json"),
            handler: "CacheFirst",
            options: {
              cacheName: "law-data-chunks-v1",
              expiration: {
                maxEntries: 120,
                maxAgeSeconds: 60 * 60 * 24 * 365,
              },
              cacheableResponse: {
                statuses: [0, 200],
              },
            },
          },
          {
            urlPattern: ({ request, url }) => {
              const assetRequest = request as unknown as { destination?: string };
              return assetRequest.destination === "script" && url.pathname.startsWith("/assets/");
            },
            handler: "CacheFirst",
            options: {
              cacheName: "lazy-script-assets",
              expiration: {
                maxEntries: 80,
                maxAgeSeconds: 60 * 60 * 24 * 365,
              },
              cacheableResponse: {
                statuses: [0, 200],
              },
            },
          },
          {
            urlPattern: ({ request, url }) => {
              const imageRequest = request as unknown as { destination?: string };
              return imageRequest.destination === "image"
                && (url.pathname.startsWith("/images/gallery/")
                  || url.pathname.startsWith("/images/concept-premiere/")
                  || url.pathname.startsWith("/images/optical-archive/")
                  || url.pathname.startsWith("/images/visual-os-v5/")
                  || url.pathname.startsWith("/images/visual-os-v6/")
                  || url.pathname.startsWith("/images/visual-os-v7/")
                  || url.pathname.startsWith("/images/visual-os-v8/"));
            },
            handler: "NetworkFirst",
            options: {
              cacheName: "visual-archive-images-v8",
              networkTimeoutSeconds: 4,
              expiration: {
                maxEntries: 220,
                maxAgeSeconds: 60 * 60 * 24 * 7,
              },
              cacheableResponse: {
                statuses: [0, 200],
              },
            },
          },
          {
            urlPattern: ({ url }) => ["/archive-manifest.json", "/story-manifest.json", "/visual-asset-manifest.json", "/route-contract.json", "/release.json"].includes(url.pathname),
            handler: "NetworkFirst",
            options: {
              cacheName: "visual-platform-manifests-v8",
              networkTimeoutSeconds: 3,
              expiration: {
                maxEntries: 6,
                maxAgeSeconds: 60 * 60,
              },
            },
          },
          {
            urlPattern: ({ url }) => {
              return url.pathname.startsWith("/api/workshops") || url.pathname.startsWith("/api/courses");
            },
            handler: "NetworkFirst",
            options: {
              cacheName: "api-data",
              expiration: {
                maxEntries: 20,
                maxAgeSeconds: 60 * 5,
              },
              cacheableResponse: {
                statuses: [0, 200],
              },
            },
          },
          {
            urlPattern: ({ url }) => {
              return url.pathname.startsWith("/api/photos");
            },
            handler: "NetworkFirst",
            options: {
              cacheName: "api-photos",
              expiration: {
                maxEntries: 1,
                maxAgeSeconds: 60 * 5,
              },
            },
          },
          {
            urlPattern: ({ url }) => {
              return url.pathname === "/api/content";
            },
            handler: "NetworkFirst",
            options: {
              cacheName: "api-content",
              expiration: {
                maxEntries: 1,
                maxAgeSeconds: 60 * 5,
              },
            },
          },
          {
            urlPattern: ({ url }) => {
              return url.pathname.startsWith("/models/");
            },
            handler: "CacheFirst",
            options: {
              cacheName: "editor-models",
              expiration: {
                maxEntries: 12,
                maxAgeSeconds: 60 * 60 * 24 * 180,
              },
            },
          },
          {
            urlPattern: ({ request, url }) => {
              const assetRequest = request as unknown as { destination?: string };
              return assetRequest.destination === "font" || url.pathname.startsWith("/fonts/");
            },
            handler: "CacheFirst",
            options: {
              cacheName: "font-assets",
              expiration: {
                maxEntries: 16,
                maxAgeSeconds: 60 * 60 * 24 * 365,
              },
            },
          },
        ],
      },
    }),
  ],
  test: {
    exclude: ["**/node_modules/**", "**/dist/**", "e2e/**", ".worktrees/**", ".tmp/**"],
  },
});
