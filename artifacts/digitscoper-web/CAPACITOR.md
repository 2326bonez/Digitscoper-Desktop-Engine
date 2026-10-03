# Digitscoper — Capacitor (native mobile) setup

The web shell in this package (`artifacts/digitscoper-web`) is Capacitor-ready.
Capacitor wraps the built web app in a native WebView. Because the shell loads
the live Digitscoper service in an iframe, the native app points at the
deployed backend — no backend code ships in the binary.

## What's already configured

- `@capacitor/core` + `@capacitor/cli` installed (devDependencies).
- `capacitor.config.ts` — appId `com.bonezlabz.digitscoper`, `webDir: dist/public`
  (matches the Vite `build.outDir`).
- `src/App.tsx` reads `VITE_API_BASE_URL` for the service iframe/links.
  Web builds default to same-origin `/api/` (unchanged behavior).
- `pnpm build:capacitor` — builds with relative asset paths (`BASE_PATH=./`,
  required for `capacitor://` / `file://`) and points the iframe at the live
  backend `https://digitscoper.onrender.com/api/`.

## Building the native app (on a machine with Android Studio / Xcode)

```bash
cd artifacts/digitscoper-web

# 1. Build the web assets for Capacitor
pnpm build:capacitor

# 2. Add native platforms (first time only — creates android/ and ios/)
npx cap add android
npx cap add ios

# 3. Sync web assets into the native projects
npx cap sync        # or: pnpm cap:sync

# 4. Open in the native IDE and run / archive
npx cap open android   # or: pnpm cap:open:android
npx cap open ios       # or: pnpm cap:open:ios
```

After changing web code, re-run `pnpm build:capacitor && npx cap sync`.

## Notes

- The web deployment on Render is untouched: the regular `pnpm build` /
  `vite build` path and same-origin `/api/` iframe are unchanged.
- `android/` and `ios/` platform folders are intentionally **not** committed —
  generate them with `npx cap add` on the build machine.
- If the backend moves to a different URL, rebuild with
  `VITE_API_BASE_URL=<new-url>`.
