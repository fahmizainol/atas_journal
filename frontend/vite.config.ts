import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    // allow requests proxied in from the tailnet (Host header = the machine's MagicDNS name)
    allowedHosts: [".tail099cd.ts.net"],
    // WSL2's inotify doesn't fire for edits made outside the watched shell (e.g.
    // by tooling on the Windows side), so native file events silently miss them
    // and HMR serves stale modules. Poll instead — a small CPU cost for reliable
    // reloads on this filesystem.
    watch: { usePolling: true },
    proxy: {
      "/api": "http://localhost:8000",
    },
  },
});
