import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import { VitePWA } from "vite-plugin-pwa";

export default defineConfig({
  plugins: [
    react(),
    // Installable on Android: served over HTTPS (Tailscale Serve) with this
    // manifest, Chrome mints a WebAPK rather than a home-screen shortcut, so the
    // journal gets its own launcher icon, its own task in recents, and no URL
    // bar. `display: standalone` is the field that decides that.
    //
    // The service worker precaches the shell only. It is deliberately NOT an
    // offline mode: every trade, tick and chart still comes from the API on the
    // desktop, so with that box asleep you get the shell and failing fetches.
    // What it buys is an instant launch and a real page instead of Chrome's
    // offline error inside a chromeless window.
    VitePWA({
      registerType: "autoUpdate",
      includeAssets: ["favicon.ico", "apple-touch-icon-180.png"],
      manifest: {
        name: "ATAS Journal",
        short_name: "Journal",
        description: "Trading journal, replay and chart workspace.",
        start_url: "/",
        scope: "/",
        display: "standalone",
        display_override: ["standalone", "minimal-ui"],
        background_color: "#0e1117",
        theme_color: "#0e1117",
        // Charts want landscape, the journal wants portrait — let the device decide.
        orientation: "any",
        icons: [
          { src: "/pwa-192.png", sizes: "192x192", type: "image/png" },
          { src: "/pwa-512.png", sizes: "512x512", type: "image/png" },
          // Without a maskable icon Android draws the square inside a white
          // circle, which is most of what makes an installed PWA look bolted on.
          {
            src: "/maskable-512.png",
            sizes: "512x512",
            type: "image/png",
            purpose: "maskable",
          },
        ],
        // Long-press the launcher icon.
        shortcuts: [
          { name: "Charts", url: "/charts" },
          { name: "Journal", url: "/journal" },
        ],
        // Reuse the open window instead of stacking instances.
        launch_handler: { client_mode: "navigate-existing" },
      },
      workbox: {
        // Shell only. `sounds/` stays off the list — it is fetched on demand and
        // has no business in a precache that every install downloads up front.
        globPatterns: ["**/*.{js,css,html,woff2,png,ico}"],
        // A navigation to /api/... must reach the server (or fail as itself);
        // answering it with index.html would turn an API error into a silent
        // blank app.
        navigateFallbackDenylist: [/^\/api\//],
        cleanupOutdatedCaches: true,
      },
    }),
  ],
  // Read .env from the repo root, so the app has one env file rather than one
  // per language. Only VITE_-prefixed keys are ever inlined into client code,
  // so the Rithmic and LLM credentials sitting in that same file stay server-side.
  envDir: "..",
  // No `build.watch` here on purpose: `pnpm dev:phone`'s watcher cannot be made
  // to poll. Vite 5 does not forward `build.watch.chokidar` to Rollup — setting
  // `usePolling` leaves the inotify watch count identical (192 either way), so
  // the option is inert and would only read as a guarantee it does not give.
  // Consequence: dev:phone can miss edits made from the Windows side, silently,
  // because a build that never runs looks like a build with nothing to do. Use
  // `pnpm dev` while actively editing — its server.watch polling does work.
  server: {
    port: 5173,
    // Windows resolves `localhost` to ::1 only, which leaves 127.0.0.1:5173
    // unreachable. Pin IPv4 (0.0.0.0 would expose it to the LAN) and browse to
    // http://127.0.0.1:5173 — `localhost` in Chrome still works via fallback.
    host: "127.0.0.1",
    // allow requests proxied in from the tailnet (Host header = the machine's MagicDNS name)
    allowedHosts: [".tail099cd.ts.net"],
    // WSL2's inotify doesn't fire for edits made outside the watched shell (e.g.
    // by tooling on the Windows side), so native file events silently miss them
    // and HMR serves stale modules. Poll instead — a small CPU cost for reliable
    // reloads on this filesystem.
    watch: { usePolling: true },
    proxy: {
      // 127.0.0.1, not localhost: uvicorn binds IPv4 only, and on Windows each
      // proxied connection would first try ::1 and eat a ~2s refused-retry.
      "/api": "http://127.0.0.1:8000",
    },
  },
});
