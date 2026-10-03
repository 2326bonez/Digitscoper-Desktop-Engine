import { useEffect, useState, type ReactNode } from 'react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { ErrorBoundary } from '@/components/error-boundary';
import { Toaster } from '@/components/ui/toaster';
import { TooltipProvider } from '@/components/ui/tooltip';
import NotFound from '@/pages/not-found';
import { ExternalLink, Radio, RefreshCw } from 'lucide-react';
import {
  Route,
  Switch,
  useLocation,
  Router as WouterRouter,
} from 'wouter';

const queryClient = new QueryClient();

// Base URL of the Digitscoper service loaded in the shell's iframe.
// Defaults to the same-origin "/api/" for web deploys. For Capacitor native
// builds, set VITE_API_BASE_URL to the live backend, e.g.
// https://digitscoper.onrender.com/api/ (see CAPACITOR.md).
const SERVICE_URL =
  import.meta.env.VITE_API_BASE_URL || '/api/';

function Home() {
  const [isLoading, setIsLoading] = useState(true);
  const [showFallback, setShowFallback] = useState(false);
  const [frameKey, setFrameKey] = useState(0);

  useEffect(() => {
    const fallbackTimer = window.setTimeout(() => setShowFallback(true), 4500);
    return () => window.clearTimeout(fallbackTimer);
  }, []);

  return (
    <main className="flex h-[100dvh] min-h-[420px] w-full flex-col bg-background">
      <header className="shell-enter z-10 flex min-h-[64px] shrink-0 items-center justify-between gap-4 border-b border-border bg-card/95 px-4 backdrop-blur-md sm:px-6">
        <div className="flex min-w-0 items-center gap-3">
          <div className="flex h-9 w-9 shrink-0 items-center justify-center rounded-md border border-primary/35 bg-primary/10 text-primary shadow-[0_0_22px_hsl(184_80%_54%/0.1)]" aria-hidden="true">
            <span className="font-mono text-sm font-semibold tracking-[-0.15em]">ds</span>
          </div>
          <div className="min-w-0">
            <p className="truncate text-sm font-semibold tracking-[0.02em] text-foreground">Digitscoper</p>
            <p className="hidden font-mono text-[10px] uppercase tracking-[0.16em] text-muted-foreground sm:block">browser console</p>
          </div>
        </div>

        <div className="flex items-center gap-3">
          <div className="hidden items-center gap-2 font-mono text-[10px] uppercase tracking-[0.12em] text-muted-foreground sm:flex">
            <span className={`h-1.5 w-1.5 rounded-full ${isLoading ? 'bg-accent' : 'bg-primary'}`} />
            {isLoading ? 'Connecting' : 'Live'}
          </div>
          <a
            href={SERVICE_URL}
            target="_blank"
            rel="noreferrer"
            data-testid="link-open-dashboard"
            className="inline-flex min-h-9 items-center gap-2 rounded-md border border-border bg-secondary px-3 font-mono text-[11px] font-medium uppercase tracking-[0.08em] text-secondary-foreground transition-colors hover:border-primary/55 hover:bg-primary/10 hover:text-primary focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
          >
            <span className="hidden sm:inline">Open direct</span>
            <ExternalLink className="h-3.5 w-3.5" aria-hidden="true" />
          </a>
        </div>
      </header>

      <section className="service-frame relative min-h-0 flex-1">
        <iframe
          key={frameKey}
          src={SERVICE_URL}
          title="Digitscoper dashboard"
          data-testid="iframe-dashboard"
          onLoad={() => setIsLoading(false)}
          className="relative z-[1]"
        />
        {isLoading && (
          <div className="pointer-events-none absolute inset-0 z-0 flex items-center justify-center">
            <div className="flex items-center gap-3 rounded-md border border-border bg-card/90 px-4 py-3 font-mono text-[11px] uppercase tracking-[0.12em] text-muted-foreground shadow-xl">
              <Radio className="h-3.5 w-3.5 animate-pulse text-primary" aria-hidden="true" />
              Loading dashboard
            </div>
          </div>
        )}
        {showFallback && (
          <div className="absolute inset-x-3 bottom-3 z-[2] flex flex-col gap-3 rounded-lg border border-accent/30 bg-card/95 p-3 shadow-2xl backdrop-blur-md sm:inset-x-auto sm:right-5 sm:w-[min(390px,calc(100%-2rem))] sm:flex-row sm:items-center sm:justify-between">
            <div className="min-w-0">
              <p className="text-sm font-medium text-foreground">Dashboard not visible?</p>
              <p className="mt-0.5 text-xs leading-relaxed text-muted-foreground">The embedded service may be blocked by your browser.</p>
            </div>
            <div className="flex shrink-0 gap-2">
              <button
                type="button"
                onClick={() => { setShowFallback(false); setIsLoading(true); setFrameKey((key) => key + 1); }}
                data-testid="button-retry-dashboard"
                className="inline-flex h-8 items-center gap-1.5 rounded-md border border-border px-2.5 font-mono text-[10px] uppercase tracking-[0.08em] text-muted-foreground transition-colors hover:border-primary/55 hover:text-primary focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
              >
                <RefreshCw className="h-3 w-3" aria-hidden="true" />
                Retry
              </button>
              <a
                href={SERVICE_URL}
                target="_blank"
                rel="noreferrer"
                data-testid="link-fallback-dashboard"
                className="inline-flex h-8 items-center gap-1.5 rounded-md bg-primary px-2.5 font-mono text-[10px] uppercase tracking-[0.08em] text-primary-foreground transition-colors hover:brightness-110 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
              >
                Open dashboard
                <ExternalLink className="h-3 w-3" aria-hidden="true" />
              </a>
            </div>
          </div>
        )}
      </section>

      <footer className="flex min-h-8 shrink-0 items-center justify-between border-t border-border bg-card px-4 font-mono text-[9px] uppercase tracking-[0.13em] text-muted-foreground sm:px-6">
        <span>Digitscoper / root shell</span>
        <span className="hidden sm:inline">Embedded service: /api/</span>
      </footer>
    </main>
  );
}

function Router() {
  return (
    // Keep a shared shell (sidebar, navbar) outside the boundary so it
    // survives a page crash.
    <RoutedErrorBoundary>
      <Switch>
        <Route path="/" component={Home} />
        <Route component={NotFound} />
      </Switch>
    </RoutedErrorBoundary>
  );
}

function RoutedErrorBoundary({ children }: { children: ReactNode }) {
  const [location] = useLocation();
  return <ErrorBoundary resetKey={location}>{children}</ErrorBoundary>;
}

function App() {
  return (
    <QueryClientProvider client={queryClient}>
      <TooltipProvider>
        <WouterRouter base={import.meta.env.BASE_URL.replace(/\/$/, '')}>
          <Router />
        </WouterRouter>
        <Toaster />
      </TooltipProvider>
    </QueryClientProvider>
  );
}

export default App;
