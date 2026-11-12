import { useEffect } from 'react';
import { Route, Routes, useNavigate } from 'react-router-dom';
import { ChatLayout } from '@/components/shell/ChatLayout';
import { ALL_ROUTES, SECTION_ROUTES } from '@/routes';
import { CommandPalette } from '@/components/overlays/CommandPalette';
import { ShortcutsModal } from '@/components/overlays/ShortcutsModal';
import { ConversationSearchModal } from '@/components/overlays/ConversationSearchModal';
import { OnboardingTour } from '@/components/overlays/OnboardingTour';
import { ProviderOnboardingModal } from '@/components/overlays/ProviderOnboardingModal';
import { BackendStatusPill } from '@/components/shell/BackendStatusPill';
import { UpdateConversation } from '@/components/overlays/UpdateConversation';
import { QuitConfirmModal } from '@/components/overlays/QuitConfirmModal';
import { UpdateRelaunchOverlay } from '@/components/overlays/UpdateRelaunchOverlay';
import { useStartupProviderRefresh } from '@/hooks/useStartupProviderRefresh';
import { useUiCustomizationSync } from '@/hooks/useUiCustomizationSync';
import { registerStreamResync } from '@/sections/chat/stream/session-subscriber';
import { resolveShortcut } from '@/lib/shortcuts';
import { toggleCommandPalette } from '@/store/command-palette';
import { toggleShortcutsModal } from '@/store/shortcuts-modal';
import { createSession } from '@/store/sessions';

/** True when keystrokes belong to a text-editing surface (skip global hotkeys). */
function isTypingTarget(target: EventTarget | null): boolean {
  if (!(target instanceof HTMLElement)) return false;
  return (
    target.tagName === 'INPUT' ||
    target.tagName === 'TEXTAREA' ||
    target.tagName === 'SELECT' ||
    target.isContentEditable
  );
}

export default function App() {
  const navigate = useNavigate();
  // Sync provider model lists from upstream once per launch so the model
  // dropdown reflects models added/removed since the app last ran.
  useStartupProviderRefresh();
  // Server-stored UI colors (model's customize_ui tool) win over the local cache.
  useUiCustomizationSync();

  // App-global SSE resync (idempotent): on focus/visibility/online, reconnect
  // any session the backend reports as streaming — covers backend-started
  // auto-turns while the user is on a non-chat route where ChatThread is not
  // mounted (its own resync effect covers the visible thread).
  useEffect(() => {
    registerStreamResync(() => Promise.resolve(null));
  }, []);

  // Global hotkeys come from lib/shortcuts.ts — the registry carries the
  // combo AND the action, so the modal cannot document a key App.tsx never
  // wires, and a binding cannot ship without an action.
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const hit = resolveShortcut(e);
      if (!hit?.action) return;
      // Keys typed into a field belong to that field, unless the binding opts
      // in (palette + pane toggles do; Ctrl+N and `?`/`,` do not).
      if (!hit.allowWhileTyping && isTypingTarget(e.target)) return;
      e.preventDefault();
      switch (hit.action) {
        case 'palette':
          toggleCommandPalette();
          break;
        case 'new-chat': {
          // ZCode-parity Ctrl+N: new chat, same path as the sidebar button
          // (creates a Tasks-home session and opens it).
          const session = createSession(null);
          void navigate(`/c/${session.id}`);
          break;
        }
        case 'toggle-sidebar':
        case 'toggle-right-drawer':
          // Claude/Hermes pane toggles (spec §3.6): Ctrl+B sidebar, Ctrl+J
          // right panel. ChatLayout owns both states, so it listens.
          window.dispatchEvent(
            new CustomEvent(
              hit.action === 'toggle-sidebar'
                ? 'august:toggle-sidebar'
                : 'august:toggle-right-drawer',
            ),
          );
          break;
        case 'settings':
          void navigate('/settings');
          break;
        case 'shortcuts':
          toggleShortcutsModal();
          break;
      }
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [navigate]);

  return (
    <>
      {/* The workspace renders IMMEDIATELY — chat surface, rail and composer
          are visible from frame one. The backend starts silently in the
          background (AGENTS.md: "An update is in-place… the download is
          automatic"; the same shape holds for boot: nothing blocks on
          backend readiness). BackendStatusPill below is the one affordance
          reporting that bootstrap, and the send path + api/client discovery
          own the "wait for the backend" half: composition works, sends park
          in the offline queue and flush the moment the API is reachable. */}
      <Routes>
          {/* The layout route's path ends in a SPLAT and its children are
              RELATIVE. It used to be a pathless parent with absolute
              children, and React Router warned that "the parent won't
              match anymore and therefore the child routes will never
              render" - and it was right, twice over.

              The descendant <Routes> that ChatLayout renders is what
              actually draws the app, and React Router only lets a
              descendant <Routes> match deeper locations when the parent
              route's path ends in "*".

              And the catch-all cannot live up here at all: ChatLayout
              renders NO <Outlet/> - the surfaces come from its own
              <Routes> - so a catch-all nested under this route was a
              branch that matched and then rendered NOTHING. An unknown
              URL (a stale bookmark, a mistyped path) showed the app
              shell with no chat in it. The catch-all is in ChatLayout's
              <Routes>, where it can actually render.

              The route table's paths stay ABSOLUTE because the nav links
              to them; they are made relative HERE, at the one place
              that needs them to be. The app's own URL shape is
              unchanged. */}
          <Route path="/*" element={<ChatLayout />}>
            {SECTION_ROUTES.map((route) => {
              const rel = route.path === '/' ? '' : route.path.replace(/^\//, '');
              if (rel === '') {
                return <Route key={route.path} index element={route.element} />;
              }
              return <Route key={route.path} path={rel} element={route.element} />;
            })}
            {ALL_ROUTES.filter((r) => r.path === '/_design').map((route) => (
              <Route key={route.path} path={route.path.replace(/^\//, '')} element={route.element} />
            ))}
          </Route>
        </Routes>
        <CommandPalette />
        <ShortcutsModal />
        <ConversationSearchModal />
        {/* First-run overlays stay off the dev-only design route: they cover
            the whole viewport with a scrim, and the /_design labs are driven by
            automated checks that click the controls underneath. */}
        {!window.location.pathname.startsWith('/_design') && (
          <>
            <OnboardingTour />
            <ProviderOnboardingModal />
          </>
        )}
        {/* First launch after a version bump: animated "what's new" chat. */}
        <UpdateConversation />
      {/* Passive status affordance for the backend bootstrap — never a
          blocker: the workspace above is already interactive. */}
      <BackendStatusPill />
      {/* Siblings of the routed tree, not inside it: the native close handler
          defers to the webview, so the quit prompt and the update-restart
          overlay must render even when a chat surface threw or the backend is
          down — a surface-scoped boundary could hide them. */}
      <QuitConfirmModal />
      {/* Rendered above everything so a stopped backend during update can't
          hide it. */}
      <UpdateRelaunchOverlay />
    </>
  );
}
