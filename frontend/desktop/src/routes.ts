/// <reference types="vite/client" />
import * as React from 'react';
import { type ReactNode, lazy, Suspense } from 'react';
import { Navigate, useParams } from 'react-router-dom';
import {
  LayoutDashboard,
  MessageSquare,
  History,
  PackageOpen,
  type LucideIcon,
} from 'lucide-react';

import { ChatThread } from '@/sections/chat/ChatThread';
import { SETTINGS_SECTIONS } from '@/settings/settings-registry';
import { PageLoader } from '@/components/PageLoader';
import { SectionBoundary } from '@/components/SectionBoundary';
import { SettingsFrameSkeleton } from '@/components/settings/SettingsFrame';

// Lazy-load heavy non-chat surfaces so the chat shell stays on the critical path.
// ONE import expression, shared by the lazy element and `preloadSettingsPage`
// below: Vite dedupes the specifier, so warming it makes `lazy()` resolve from
// the module that is already in flight rather than issuing a second fetch.
function loadSettingsPage() {
  return import('@/sections/settings/SettingsPage');
}
const SettingsPage = lazy(() =>
  loadSettingsPage().then((m) => ({ default: m.SettingsPage })),
);
const RunsPage = lazy(() =>
  import('@/sections/runs/RunsPage').then((m) => ({ default: m.RunsPage })),
);
const ArtifactsPage = lazy(() =>
  import('@/sections/artifacts/ArtifactsPage').then((m) => ({ default: m.ArtifactsPage })),
);
// Dev-only. The `lazy()` CALL lives inside the branch on purpose: at module top
// level it is a side-effecting call Rollup cannot drop, which is how the lab —
// base64 PDF fixture included — stayed in the production bundle as a 24,111-byte
// chunk (`DesignRoute-*.js`) while the route below claimed to be tree-shaken.
const DesignRoute = import.meta.env.DEV
  ? lazy(() => import('@/pages/DesignRoute').then((m) => ({ default: m.DesignRoute })))
  : null;

const DocsPage = lazy(() =>
  import('@/sections/docs/DocsPage').then((m) => ({ default: m.DocsPage })),
);

function Lazy({ children }: { children: ReactNode }) {
  return React.createElement(Suspense, { fallback: React.createElement(PageLoader) }, children);
}

/** Lazy + SectionBoundary: prevents one crashed section from taking down the app. */
function LazySection({ name, children }: { name: string; children?: ReactNode }) {
  return React.createElement(
    SectionBoundary,
    { name },
    React.createElement(Suspense, { fallback: React.createElement(PageLoader) }, children),
  );
}

export interface SectionRoute {
  path: string;
  label: string;
  Icon: LucideIcon;
  element: ReactNode;
  nav?: boolean;
}

export interface NavItem {
  to: string;
  label: string;
  Icon: LucideIcon;
  visible?: () => boolean | Promise<boolean>;
}

export interface SettingsTab {
  key: string;
  label: string;
  Icon: LucideIcon;
  path: string;
}

function ChatThreadWithParams() {
  const { sessionId } = useParams<{ sessionId: string }>();
  return React.createElement(ChatThread, { sessionId: sessionId ?? null });
}

export const SECTION_ROUTES: readonly SectionRoute[] = [
  // ChatLayout redirects `/` to a real session; null avoids a fake "demo" id.
  { path: '/', label: 'Chat', Icon: MessageSquare, element: React.createElement(ChatThread, { sessionId: null }), nav: true },
  { path: '/c/:sessionId', label: 'Chat', Icon: MessageSquare, element: React.createElement(ChatThreadWithParams) },
  {
    /* Part D diet — Automations is FOLDED INTO Runs (a section there; the
     * manual create form is gone; creation is agent-driven). This entry only
     * redirects old /automations deep links. */
    path: '/automations',
    label: 'Runs',
    Icon: History,
    element: React.createElement(Navigate, { to: '/runs', replace: true }),
  },
  {
    path: '/runs',
    label: 'Runs',
    Icon: History,
    element: React.createElement(LazySection, { name: 'Runs' }, React.createElement(RunsPage)),
    nav: true,
  },
  {
    /* The Board page is FOLDED INTO Runs (the board is a section there);
     * this entry only redirects old /board deep links. */
    path: '/board',
    label: 'Runs',
    Icon: History,
    element: React.createElement(Navigate, { to: '/runs', replace: true }),
  },
  {
    /* Part D diet — Charts is DELETED (trading charts belong to August
     * Trading, not the researcher/programmer harness). Deep links go to chat;
     * the charts SKILL (agent-made visuals via artifacts) is unaffected. */
    path: '/charts',
    label: 'Chat',
    Icon: MessageSquare,
    element: React.createElement(Navigate, { to: '/', replace: true }),
  },
  {
    /* Part D diet — Artifacts stays deep-linkable but is no longer a
     * top-level screen: the rail's Artifacts row and the right drawer are its
     * compact doors. */
    path: '/artifacts',
    label: 'Artifacts',
    Icon: PackageOpen,
    element: React.createElement(LazySection, { name: 'Artifacts' }, React.createElement(ArtifactsPage)),
  },
  {
    /* Live/voice is DELETED (out of scope for v1 — docs/agent/product.md).
     * The old /live deep link redirects to chat; the surface code is gone. */
    path: '/live',
    label: 'Chat',
    Icon: MessageSquare,
    element: React.createElement(Navigate, { to: '/', replace: true }),
  },
  {
    /* Part D diet — History is MERGED INTO the sidebar's Threads list (the
     * rail IS the history). Deep links land in chat. */
    path: '/history',
    label: 'Chat',
    Icon: MessageSquare,
    element: React.createElement(Navigate, { to: '/', replace: true }),
  },
  {
    /* Part D diet — Learning is FOLDED INTO the Memory & Skills settings.
     * Deep links land on the Memory & Knowledge section. */
    path: '/learning',
    label: 'Settings',
    Icon: PackageOpen,
    element: React.createElement(Navigate, { to: '/settings/memory-knowledge', replace: true }),
  },
  {
    /* The Nessa Docs reader: a project's `docs/state/` map, rendered from the
     * catalog the backend validates. Part D diet — not a top-level screen;
     * the rail's More row is its compact door. */
    path: '/docs',
    label: 'Docs',
    Icon: PackageOpen,
    element: React.createElement(LazySection, { name: 'Docs' }, React.createElement(DocsPage)),
  },
] as const;

/* Hidden-tier settings stay deep-linkable and searchable, but must not be
 * surfaced by the command palette any more than they are in the rail. */
export const SETTINGS_TABS: readonly SettingsTab[] = SETTINGS_SECTIONS.filter((section) => section.tier !== 'hidden').map((section) => ({
  key: section.id,
  label: section.label,
  Icon: section.icon,
  path: `/settings/${section.id}`,
}));

/* One shared route renders for both /settings and /settings/:section.
 * ChatLayout mounts it as a DIALOG over the background-location chat (see
 * ChatLayout's background/overlay <Routes> split); App.tsx declares the paths
 * with a null element for matching and deep links. Section content remounts
 * inside SettingsPage so each tab refetches live data on entry.
 *
 * The rail-not-switching defect this used to blame on React is not real. A
 * hoisted `React.createElement(...)` element IS referentially identical across
 * renders, and the claim was that React therefore bails out of re-rendering it —
 * measured in `test/settingsRouteElement.test.tsx`: the subtree re-renders and
 * `useParams().section` updates, exactly as it does through this component. What
 * actually froze the panel was the `AnimatePresence mode="wait"` that used to
 * wrap the section body (removed; see SettingsPage's comment). This stays a
 * component because a component is what a route element is, not because a
 * constant would have broken. */
export function SettingsPageRoute(): ReactNode {
  return React.createElement(
    Suspense,
    { fallback: React.createElement(SettingsFrameSkeleton) },
    React.createElement(SettingsPage),
  );
}

/** Fetch the settings chunk before it is asked for.
 *
 *  The panel is the most-opened surface after the chat, and its chunk is 7 kB
 *  gzipped — cheaper than the frame-less loading state it buys back. Fired from
 *  main.tsx's idle deferral, so it never competes with the first paint.
 *
 *  This is a latency fix, not the correctness fix: `SettingsFrameSkeleton` is
 *  what makes a COLD first open (idle callback starved, slow disk, a dev
 *  reload that lands straight on Customize) look like the dialog opening
 *  instead of a stray list under the page you were on. */
export function preloadSettingsPage(): void {
  void loadSettingsPage();
}

/* Dev-only design token inspector. `DesignRoute` is null in a production build
 * (see its declaration), so neither the route nor the import survives it. */
const DEV_ROUTES: readonly SectionRoute[] = import.meta.env.DEV && DesignRoute
  ? [
      {
        path: '/_design',
        label: 'Design',
        Icon: LayoutDashboard,
        element: React.createElement(Lazy, null, React.createElement(DesignRoute)),
        nav: false,
      },
    ]
  : [];

/** Everything App.tsx's tree mounts from a flat table: the chat surfaces and
 *  the dev-only inspector. `/settings*` is deliberately NOT here — App.tsx
 *  declares those paths with a null element (for matching and deep links)
 *  while ChatLayout renders the dialog itself; a SETTINGS_ROUTES entry here
 *  was a second, never-mounted declaration of the same paths. */
export const ALL_ROUTES: readonly SectionRoute[] = [
  ...SECTION_ROUTES,
  ...DEV_ROUTES,
];

/** Chat surfaces that keep rendering BEHIND the settings dialog. ChatLayout
 *  mounts these against the BACKGROUND location while /settings/* is open, so
 *  the dialog floats over a live app instead of a route swap that unmounts
 *  the chat. App.tsx's tree still declares everything for matching and deep
 *  links; this list is the background half of that split. */
export const CHAT_SURFACE_ROUTES: readonly SectionRoute[] = [
  ...SECTION_ROUTES,
  ...DEV_ROUTES,
];

export const SECTION_NAV_ITEMS: readonly NavItem[] = SECTION_ROUTES.filter((route) => route.nav).map((route) => ({
  to: route.path,
  label: route.label,
  Icon: route.Icon,
}));
