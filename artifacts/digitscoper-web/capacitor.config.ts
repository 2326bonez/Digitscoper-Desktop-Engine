import type { CapacitorConfig } from '@capacitor/cli';

const config: CapacitorConfig = {
  appId: 'com.bonezlabz.digitscoper',
  appName: 'Digitscoper',
  // Must match vite.config.ts `build.outDir` (artifacts/digitscoper-web/dist/public).
  webDir: 'dist/public',
  // The bundled web shell is a thin wrapper that loads the live Digitscoper
  // service in an iframe (see VITE_API_BASE_URL in App.tsx), so the native
  // app stays in sync with the deployed backend.
  server: {
    androidScheme: 'https',
  },
};

export default config;
