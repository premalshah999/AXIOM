import React, { lazy, Suspense, useCallback, useEffect, useMemo, useRef, useState } from "react";
import { createRoot } from "react-dom/client";
import { LegalGate } from "./legal";
import { LicenseBanner, LicenseGate } from "./license";
import "./styles/tokens.css";
import "./styles/base.css";
import "./styles/components.css";
import "./styles/shell.css";
// Check, Segmented, folds and the other small pieces of Profile, Settings and sign-in that the primitives share.
import "./styles/setup.css";
import { api, ApiError, SIGNED_OUT, signedOut } from "./api";
import {
  canonical,
  NO_COUNTS,
  parseRoute,
  routeUrl,
  WorkspaceContext,
  type AnyRoute,
  type Route,
  type Workspace,
} from "./context";
import { ToastContext, usePoll, type Toast } from "./hooks";
import { deriveRows, withMarks, type Marks } from "./model";
import type { Snapshot } from "./types";
import { ago } from "./format";
import { NeedsView } from "./needs";
import { countQuestions } from "./questions";
import { Shell } from "./shell/Shell";
import { StopFloating } from "./shell/StopControl";
import { isLive, panelState, statusLine } from "./identity/state";
import { Wordmark } from "./shell/Brand";
import { CommandPalette } from "./shell/CommandPalette";
import { ComingSoon } from "./shell/ComingSoon";
import { ALL_ITEMS, titleFor } from "./shell/nav";
import { Button, Dialog, Loading, ToastStack } from "./ui/index";
import { BatchView } from "./batch";
import { BoardView } from "./jobboard";
import { LedgerView } from "./ledger";
import { QueueView } from "./queue";
import { ReviewView } from "./review";
import { JobPanel } from "./jobdetail";
import { AddJobDialog } from "./jobintake";
import type { AccountStatus } from "./account";
import { guideLater } from "./guide";
import { hasUnsaved, useLeaveWarning } from "./shell/unsaved";

// Profile, Resumes, Settings, the resume editor, sign-in and the setup guide are most of the dashboard's code and are rarely open:
// they load when shown, so Batch and Jobs paint from a smaller bundle (2026-10-23).
const ProfileView = lazy(() => import("./profile").then((m) => ({ default: m.ProfilePage })));
const ResumesView = lazy(() => import("./resumes").then((m) => ({ default: m.ResumesView })));
const SettingsView = lazy(() => import("./settings").then((m) => ({ default: m.SettingsView })));
const ResumeEditor = lazy(() => import("./editor").then((m) => ({ default: m.ResumeEditor })));
const TailorView = lazy(() => import("./tailoring").then((m) => ({ default: m.TailorView })));
const AuthScreen = lazy(() => import("./account").then((m) => ({ default: m.AuthScreen })));
const Onboarding = lazy(() => import("./onboarding").then((m) => ({ default: m.Onboarding })));

const unfinished = () => (guideLater() ? "later" : "needed");

function App() {
  const [auth, setAuth] = useState<"unknown" | "in" | "out">("unknown");
  // Why the sign-in screen is showing: "Your session ended. Sign in again.", "You signed out." ...
  const [signOutReason, setSignOutReason] = useState("");
  const [setupGuide, setSetupGuide] = useState<"unknown" | "needed" | "done" | "later">("unknown");
  const [route, setRoute] = useState<Route>(() => parseRoute());
  const [toasts, setToasts] = useState<Toast[]>([]);
  const [who, setWho] = useState<{ name?: string; email?: string }>({});
  const [palette, setPalette] = useState(false);
  // Where you are, for the one question below: moving to another page or Settings section with unsaved changes asks first.
  const here = useRef(route);
  here.current = route;
  const [leaving, setLeaving] = useState<Route | null>(null);
  // Opening a job's panel or changing a filter stays on the page; only a different page or section loses the form.
  const losesForm = (next: Route) => hasUnsaved() && (next.view !== here.current.view || next.tab !== here.current.tab);

  const notify = useCallback((tone: Toast["tone"], text: string) => {
    const id = Date.now() + Math.random();
    // The same message twice (a bulk action whose every job fails the same way) shows once.
    setToasts((list) => (list.some((t) => t.tone === tone && t.text === text) ? list : [...list.slice(-3), { id, tone, text }]));
    if (tone !== "error")
      window.setTimeout(
        () => setToasts((list) => list.filter((t) => t.id !== id)),
        6500,
      );
  }, []);

  useEffect(() => {
    const out = (event: Event) => {
      const detail = (event as CustomEvent<string>).detail;
      // A first visit without a session needs no explanation; a session that ended does. A poll that was in flight
      // when the owner signed out answers 401 afterwards and must not replace "You signed out."
      const reason = typeof detail === "string" && !/local access token/i.test(detail) ? detail : "";
      setSignOutReason((current) => reason || current);
      setAuth("out");
    };
    // An old link (?view=setup&tab=email) is rewritten to its new home in the address bar; the page was already told.
    const pop = () => {
      const next = parseRoute();
      // Back or Forward: the browser has already moved, so the address goes back while the question is asked.
      if (losesForm(next)) {
        history.pushState(null, "", routeUrl(here.current));
        setLeaving(next);
        return;
      }
      const address = location.pathname + location.search + location.hash;
      if (routeUrl(next) !== address) history.replaceState(null, "", routeUrl(next));
      setRoute(next);
    };
    pop();
    window.addEventListener(SIGNED_OUT, out);
    window.addEventListener("popstate", pop);
    return () => {
      window.removeEventListener(SIGNED_OUT, out);
      window.removeEventListener("popstate", pop);
    };
  }, []);

  useLeaveWarning();
  const move = useCallback((next: Route) => {
    history.pushState(null, "", routeUrl(next));
    setRoute(next);
  }, []);
  const go = useCallback(
    (raw: AnyRoute) => {
      const next = canonical(raw);
      if (losesForm(next)) setLeaving(next);
      else move(next);
    },
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [move],
  );

  // Ask the public account endpoint first, so a signed-out visit shows sign-in without a failed request.
  useEffect(() => {
    if (auth !== "unknown") return;
    let current = true;
    api<AccountStatus>("/account")
      .then((s) => {
        if (!current) return;
        setAuth(s.signed_in ? "in" : "out");
        // The Mac app's window signs itself in again when told (desktop/AXIOM.m), as it does for a 401: this answer is not one.
        if (!s.signed_in) signedOut();
        if (s.signed_in) setWho({ name: s.name, email: s.email });
        // No session on arrival, as after Delete everything: the Mac window listens for this and signs itself in. A browser just stays
        // on the sign-in screen it was going to show.
        else signedOut();
        if (s.signed_in && s.onboarding)
          setSetupGuide((g) => (g === "unknown" ? (s.onboarding!.completed ? "done" : unfinished()) : g));
      })
      // Unreachable server: let the snapshot load report the error (with Try again).
      .catch(() => current && setAuth("in"));
    return () => {
      current = false;
    };
  }, [auth]);

  // Overview and Queue show live state; other views only need occasional freshness.
  const live = route.view === "batch" || route.view === "queue" || !!route.job;
  const snap = usePoll<Snapshot>(
    (signal) =>
      api<Snapshot>("/snapshot?include_sources=false", "GET", undefined, signal),
    live ? 6000 : 20000,
    [live],
    auth === "in",
  );
  useEffect(() => {
    if (snap.data) setAuth("in");
  }, [snap.data]);

  // A new account (or an unfinished profile) opens the setup guide once after sign-in.
  useEffect(() => {
    if (auth !== "in" || setupGuide !== "unknown") return;
    api<AccountStatus>("/account")
      .then((s) => setSetupGuide(s.onboarding && !s.onboarding.completed ? unfinished() : "done"))
      .catch(() => setSetupGuide("done"));
  }, [auth, setupGuide]);

  // Bumped by every refresh, so paged lists (Pipeline, Discovered, a job's attempts) re-read after an action.
  const [version, setVersion] = useState(0);
  const snapRefresh = snap.refresh;
  const refresh = useCallback(async () => {
    setVersion((v) => v + 1);
    await snapRefresh();
  }, [snapRefresh]);
  const [marks, setMarks] = useState<Marks>({});
  const markJobs = useCallback((ids: string[], state: string) => {
    const at = Date.now();
    setMarks((current) => {
      // Marks older than a few minutes were confirmed long ago; drop them as new ones arrive.
      const kept = Object.fromEntries(
        Object.entries(current).filter(([, mark]) => at - mark.at < 300_000),
      );
      for (const id of ids) kept[id] = { state, at };
      return kept;
    });
  }, []);
  const derived = useMemo(
    () => (snap.data ? deriveRows(snap.data) : []),
    [snap.data],
  );
  const rows = useMemo(
    () => withMarks(derived, marks, snap.requestedAt),
    [derived, marks, snap.requestedAt],
  );
  const lost = snap.failures >= 2;
  const workspace = useMemo<Workspace | null>(
    () =>
      snap.data && {
        snapshot: snap.data,
        rows,
        counts: snap.data.counts || NO_COUNTS,
        refresh,
        version,
        link: { lost, updatedAt: snap.updatedAt },
        marks,
        markJobs,
        route,
        go,
        openJob: (job, run) => go({ ...route, job, run }),
      },
    [snap.data, rows, refresh, version, lost, snap.updatedAt, marks, markJobs, route, go],
  );

  // The Mac app's Dock badge (desktop/AXIOM.m); a browser has no such handler.
  const waitingQuestions = snap.data ? countQuestions(snap.data.reviews) : 0;
  useEffect(() => {
    (window as unknown as { webkit?: { messageHandlers?: { axiom?: { postMessage: (m: string) => void } } } }).webkit?.messageHandlers?.axiom?.postMessage("needs:" + waitingQuestions);
  }, [waitingQuestions]);

  useEffect(() => {
    const label = titleFor(route);
    document.title = `${label} · AXIOM`;
  }, [snap.data, route]);

  // Vermilion means live, and only real state turns it on: the lamp in the wordmark, every running lamp.
  const panel = snap.data ? panelState(snap.data, lost) : null;
  const lit = !!snap.data && !!panel && isLive(snap.data, panel);
  useEffect(() => {
    document.documentElement.dataset.run = lit ? "on" : "off";
  }, [lit]);

  // ⌘K / Ctrl-K opens the command palette from anywhere once the workspace is up.
  useEffect(() => {
    const key = (e: KeyboardEvent) => {
      if ((e.metaKey || e.ctrlKey) && !e.altKey && e.key.toLowerCase() === "k") {
        e.preventDefault();
        setPalette((open) => !open);
      }
    };
    window.addEventListener("keydown", key);
    return () => window.removeEventListener("keydown", key);
  }, []);

  // Screens outside the shell (sign-in, the setup guide, boot) are their own scroll region: the document never scrolls.
  if (auth === "out")
    return (
      <div className="ax-screen">
        <Suspense fallback={<Loading label="Opening sign-in…" />}>
          <AuthScreen
            notice={signOutReason}
            onSignedIn={async () => {
              setSignOutReason("");
              setAuth("unknown");
              setSetupGuide("unknown");
              await snap.refresh();
            }}
          />
        </Suspense>
      </div>
    );

  if (setupGuide === "needed")
    return (
      <LegalGate>
      <div className="ax-screen">
        {/* A guide reopened while a batch runs: Stop is on this screen too. */}
        <StopFloating />
        <Suspense fallback={<Loading label="Opening the setup guide…" />}>
          <Onboarding
            onDone={() => {
              guideLater(false);
              setSetupGuide("done");
              snap.refresh();
              go({ view: "batch" });
            }}
            onExit={() => {
              guideLater(true);
              setSetupGuide("later");
            }}
          />
        </Suspense>
      </div>
      </LegalGate>
    );

  if (!workspace)
    return (
      <div className="ax-screen">
        <div className="ax-boot">
          <Wordmark width={108} />
          {snap.error &&
          !(snap.error instanceof ApiError && snap.error.status === 401) ? (
            <div className="ax-boot-error" role="alert">
              <p>{snap.error.message}</p>
              <Button onClick={() => snap.refresh()}>Try again</Button>
            </div>
          ) : (
            <Loading label="Opening your workspace…" />
          )}
        </div>
      </div>
    );

  const { snapshot } = workspace;
  // The Needs you count is what the page shows: one per distinct question, not one per application.
  const questions = countQuestions(snapshot.reviews);
  const soon = ALL_ITEMS.find((i) => i.route.view === route.view && i.soon);

  return (
    <LegalGate>
    <ToastContext.Provider value={notify}>
      <WorkspaceContext.Provider value={workspace}>
        <Shell
          route={route}
          go={go}
          needs={questions}
          who={who}
          status={statusLine(snapshot, panel!)}
          onSearch={() => setPalette(true)}
          onSignOut={async () => {
            try {
              await api("/logout", "POST");
              signedOut("You signed out.");
            } catch (e) {
              notify("error", (e as Error).message);
            }
          }}
          banners={
            <>
              <LicenseBanner />
              {setupGuide === "later" && (
                <div className="ax-banner" role="status">
                  <span>Your profile setup isn't finished.</span>
                  <button
                    type="button"
                    className="ax-link"
                    onClick={() => {
                      guideLater(false);
                      setSetupGuide("needed");
                    }}
                  >
                    Continue setup
                  </button>
                </div>
              )}
              {/* Overview's own sentence already says so. */}
              {lost && route.view !== "batch" && (
                <div className="ax-banner is-bad" role="status">
                  <span>
                    Connection lost{snap.error ? ` (${snap.error.message})` : ""}. Showing data from {ago(snap.updatedAt)};
                    retrying automatically.
                  </span>
                </div>
              )}
            </>
          }
        >
          {route.view === "batch" && <BatchView />}
          {route.view === "review" && <ReviewView />}
          {route.view === "queue" && <QueueView />}
          {route.view === "board" && <BoardView />}
          {route.view === "ledger" && <LedgerView />}
          {route.view === "needs" && <NeedsView />}
          {soon && <ComingSoon item={soon} />}
          <Suspense fallback={<Loading />}>
            {route.view === "profile" && <ProfileView />}
            {route.view === "resumes" && <ResumesView />}
            {route.view === "settings" && <SettingsView />}
            {route.view === "editor" && <ResumeEditor />}
            {route.view === "tailor" && <TailorView />}
          </Suspense>
        </Shell>
        {route.job && (
          <JobPanel
            key={route.job + (route.run || "")}
            jobId={route.job}
            runId={route.run}
            close={() => go({ ...route, job: undefined, run: undefined })}
          />
        )}
        <AddJobDialog />
        <Dialog
          open={leaving !== null}
          onClose={() => setLeaving(null)}
          title="Leave without saving?"
          sub="You changed something on this page and didn't save it. If you leave now, the change is lost."
          footer={
            <>
              <Button variant="ghost" onClick={() => {
                if (leaving) move(leaving);
                setLeaving(null);
              }}>
                Leave and lose the change
              </Button>
              <Button variant="primary" onClick={() => setLeaving(null)}>
                Keep editing
              </Button>
            </>
          }
        >
          <p className="ax-hint">Press Save at the bottom of the page to keep it.</p>
        </Dialog>
        <CommandPalette open={palette} onClose={() => setPalette(false)} />
        <ToastStack toasts={toasts} onDismiss={(id) => setToasts((list) => list.filter((x) => x.id !== id))} />
      </WorkspaceContext.Provider>
    </ToastContext.Provider>
    </LegalGate>
  );
}

createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <LicenseGate>
      <App />
    </LicenseGate>
  </React.StrictMode>,
);
