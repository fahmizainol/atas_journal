import { defineConfig } from "vite";

/**
 * Serving a production build locally, for measuring.
 *
 * `vite preview` does not read `server.proxy` — only `preview.proxy` — so a
 * built bundle served by it cannot reach the API without this file, and every
 * chart comes up empty. Its own config rather than a branch in vite.config.ts
 * because the dev server must not grow a second way to be configured.
 *
 * Why bother serving a build at all: on the dev server roughly 40% of a playing
 * chart's frame time is React's `jsxDEV` and `validatePropertiesInDevelopment`,
 * overhead that ships to nobody — the same page measures 33 fps on :5173 and
 * 56 fps built. Any performance number taken against :5173 is fiction.
 *
 *   pnpm --dir frontend build
 *   pnpm --dir frontend exec vite preview --config vite.preview.config.ts
 *   APP_URL=http://localhost:4300 node tools/browser/renderbudget.mjs
 */
export default defineConfig({
  preview: {
    port: 4300,
    strictPort: true,
    proxy: { "/api": "http://localhost:8000" },
  },
});
