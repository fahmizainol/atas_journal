/// <reference types="vite/client" />

// The env keys this app reads at build time. Vite inlines only VITE_-prefixed
// values, and reads them from the repo-root .env (see vite.config.ts envDir),
// which is the same file the API's credentials live in — those have no prefix
// and so never reach the browser.
interface ImportMetaEnv {
  /** PostHog project key. Unset disables usage tracking entirely: nothing is
   *  imported, nothing is sent, and the app makes no external calls at all. */
  readonly VITE_POSTHOG_KEY?: string;
  /** PostHog ingestion host. Defaults to US cloud. */
  readonly VITE_POSTHOG_HOST?: string;
}

interface ImportMeta {
  readonly env: ImportMetaEnv;
}
