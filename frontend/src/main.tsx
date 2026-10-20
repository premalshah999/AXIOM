import React, { useCallback, useEffect, useMemo, useState } from "react";
import { createRoot } from "react-dom/client";
import { X } from "lucide-react";
import "@fontsource-variable/dm-sans/opsz.css";
import "./style.css";
import { api, ApiError, SIGNED_OUT } from "./api";
import {
  parseRoute,
  routeUrl,
  WorkspaceContext,
  type Route,
  type View,
  type Workspace,
} from "./context";
import { ToastContext, usePoll, type Toast } from "./hooks";
import { deriveRows } from "./model";
import type { Snapshot } from "./types";
import { ago, Loading } from "./ui";
import { BatchView } from "./batch";
import { JobsView } from "./jobs";
import { LedgerView } from "./ledger";
import { SetupView } from "./setup";
import { JobPanel } from "./jobdetail";
import { JobLinkIntake } from "./jobintake";
import { ResumeEditor } from "./editor";
import { AuthScreen, guideLater, Onboarding, type AccountStatus } from "./onboarding";

const NAV: { view: View; label: string }[] = [
  { view: "batch", label: "Batch" },
  { view: "jobs", label: "Jobs" },
  { view: "ledger", label: "Ledger" },
  { view: "setup", label: "Setup" },
];

export function Mark() {
  // Three cells of the progress tape: the product's one recurring motif.
  return (
    <svg className="mark" viewBox="0 0 20 20" aria-hidden="true">
      <rect x="2" y="3" width="4" height="14" rx="1" className="mark-on" />
      <rect x="8" y="3" width="4" height="14" rx="1" className="mark-on" />
      <rect x="14" y="3" width="4" height="14" rx="1" className="mark-off" />
    </svg>
  );
}

const unfinished = () => (guideLater() ? "later" : "needed");

function App() {
  const [auth, setAuth] = useState<"unknown" | "in" | "out">("unknown");
  const [setupGuide, setSetupGuide] = useState<"unknown" | "needed" | "done" | "later">("unknown");
  const [route, setRoute] = useState<Route>(() => parseRoute());
  const [toasts, setToasts] = useState<Toast[]>([]);

  const notify = useCallback((tone: Toast["tone"], text: string) => {
    const id = Date.now() + Math.random();
    setToasts((list) => [...list.slice(-3), { id, tone, text }]);
    if (tone !== "error")
      window.setTimeout(
        () => setToasts((list) => list.filter((t) => t.id !== id)),
        6500,
      );
  }, []);

  useEffect(() => {
    const out = () => setAuth("out");
    const pop = () => setRoute(parseRoute());
    window.addEventListener(SIGNED_OUT, out);
    window.addEventListener("popstate", pop);
    return () => {
      window.removeEventListener(SIGNED_OUT, out);
      window.removeEventListener("popstate", pop);
    };
  }, []);

  const go = useCallback((next: Route) => {
    const sameView = next.view === parseRoute().view;
    history.pushState(null, "", routeUrl(next));
    setRoute(next);
    if (!sameView) window.scrollTo({ top: 0 });
  }, []);

  // Ask the public account endpoint first, so a signed-out visit shows sign-in without a failed request.
  useEffect(() => {
    if (auth !== "unknown") return;
    let current = true;
    api<AccountStatus>("/account")
      .then((s) => {
        if (!current) return;
        setAuth(s.signed_in ? "in" : "out");
        if (s.signed_in && s.onboarding)
          setSetupGuide((g) => (g === "unknown" ? (s.onboarding!.completed ? "done" : unfinished()) : g));
      })
      // Unreachable server: let the snapshot load report the error (with Try again).
      .catch(() => current && setAuth("in"));
    return () => {
      current = false;
    };
  }, [auth]);

  // Batch and Jobs show live state; other views only need occasional freshness.
  const live = route.view === "batch" || route.view === "jobs" || !!route.job;
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

  const rows = useMemo(
    () => (snap.data ? deriveRows(snap.data) : []),
    [snap.data],
  );
  const workspace = useMemo<Workspace | null>(
    () =>
      snap.data && {
        snapshot: snap.data,
        rows,
        refresh: snap.refresh,
        route,
        go,
        openJob: (job, run) => go({ ...route, job, run }),
      },
    [snap.data, rows, snap.refresh, route, go],
  );

  useEffect(() => {
    const rt = snap.data?.autopilot_runtime;
    const label = NAV.find((n) => n.view === route.view)?.label || "";
    document.title =
      rt?.state === "active"
        ? `${rt.confirmed ?? 0}/${rt.target ?? 100} · ${label} · ApplyPilot`
        : `${label} · ApplyPilot`;
  }, [snap.data, route.view]);

  if (auth === "out")
    return (
      <AuthScreen
        onSignedIn={async () => {
          setAuth("unknown");
          setSetupGuide("unknown");
          await snap.refresh();
        }}
      />
    );

  if (setupGuide === "needed")
    return (
      <Onboarding
        onDone={() => {
          guideLater(false);
          setSetupGuide("done");
          snap.refresh();
          go({ view: "batch" });
          // go() keeps the scroll position within a view; the guide's long review page is not Batch.
          window.scrollTo({ top: 0 });
        }}
        onExit={() => {
          guideLater(true);
          setSetupGuide("later");
          window.scrollTo({ top: 0 });
        }}
      />
    );

  if (!workspace)
    return (
      <div className="boot">
        <div className="brand">
          <Mark />
          <span>ApplyPilot</span>
        </div>
        {snap.error &&
        !(snap.error instanceof ApiError && snap.error.status === 401) ? (
          <div className="boot-error" role="alert">
            <p>{snap.error.message}</p>
            <button className="btn" onClick={() => snap.refresh()}>
              Try again
            </button>
          </div>
        ) : (
          <Loading label="Opening your workspace…" />
        )}
      </div>
    );

  const { snapshot } = workspace;
  const rt = snapshot.autopilot_runtime || {};
  const questions = snapshot.reviews.length;
  const intake = snapshot.control.paused
    ? { tone: "idle", text: "Paused" }
    : rt.state === "complete"
      ? { tone: "ok", text: "Target reached" }
      : rt.intake_enabled
        ? { tone: "run", text: rt.intake_reason || "Running" }
        : rt.state === "active"
          ? { tone: "fix", text: rt.intake_reason || "Held" }
          : { tone: "idle", text: "No batch" };

  return (
    <ToastContext.Provider value={notify}>
      <WorkspaceContext.Provider value={workspace}>
        <a className="skip" href="#main">
          Skip to content
        </a>
        <header className="bar">
          <a
            className="brand"
            href={routeUrl({ view: "batch" })}
            onClick={(e) => {
              e.preventDefault();
              go({ view: "batch" });
            }}
          >
            <Mark />
            <span>ApplyPilot</span>
          </a>
          <nav className="nav" aria-label="Main navigation">
            {NAV.map((n) => (
              <a
                key={n.view}
                href={routeUrl({ view: n.view })}
                aria-current={
                  route.view === n.view || (route.view === "editor" && n.view === "setup")
                    ? "page"
                    : undefined
                }
                onClick={(e) => {
                  e.preventDefault();
                  go({ view: n.view });
                }}
              >
                <span>{n.label}</span>
                {n.view === "batch" && questions > 0 && (
                  <b className="nav-count">
                    {questions}
                    <span className="sr-only">
                      {" "}
                      question{questions === 1 ? "" : "s"} need you
                    </span>
                  </b>
                )}
              </a>
            ))}
          </nav>
          <div className="bar-end">
            <button
              className={"intake-chip tone-" + intake.tone}
              onClick={() => go({ view: "batch" })}
            >
              <i aria-hidden="true" />
              <span>{intake.text}</span>
            </button>
            <div className="intake-slot">
              <JobLinkIntake
                request={api}
                refresh={snap.refresh}
                autoApply={
                  snapshot.profile.autonomous &&
                  snapshot.control.auto_submit &&
                  !snapshot.control.paused
                }
                onImported={(job, message) => {
                  notify("ok", message);
                  go({ ...route, job: job.id, run: undefined });
                }}
              />
            </div>
          </div>
        </header>
        {setupGuide === "later" && (
          <div className="setup-banner" role="status">
            <span>Your profile setup isn't finished. ApplyPilot answers applications from it.</span>
            <button
              className="btn sm primary"
              onClick={() => {
                guideLater(false);
                setSetupGuide("needed");
              }}
            >
              Continue setup
            </button>
          </div>
        )}
        {snap.failures >= 2 && (
          <div className="conn" role="status">
            <span>
              Connection lost{snap.error ? ` (${snap.error.message})` : ""}.
              Showing data from {ago(snap.updatedAt)}; retrying automatically.
            </span>
          </div>
        )}
        <main id="main" className={"main view-" + route.view} tabIndex={-1}>
          {route.view === "batch" && <BatchView />}
          {route.view === "jobs" && <JobsView />}
          {route.view === "ledger" && <LedgerView />}
          {route.view === "setup" && <SetupView />}
          {route.view === "editor" && <ResumeEditor />}
        </main>
        {route.job && (
          <JobPanel
            key={route.job + (route.run || "")}
            jobId={route.job}
            runId={route.run}
            close={() => go({ ...route, job: undefined, run: undefined })}
          />
        )}
        <div className="toasts">
          <div aria-live="polite" role="status" className="toast-stack">
            {toasts
              .filter((t) => t.tone !== "error")
              .map((t) => (
                <div key={t.id} className={"toast " + t.tone}>
                  {t.text}
                </div>
              ))}
          </div>
          <div role="alert" className="toast-stack">
            {toasts
              .filter((t) => t.tone === "error")
              .map((t) => (
                <div key={t.id} className="toast error">
                  <span>{t.text}</span>
                  <button
                    className="icon-btn"
                    aria-label="Dismiss message"
                    onClick={() =>
                      setToasts((list) => list.filter((x) => x.id !== t.id))
                    }
                  >
                    <X size={16} />
                  </button>
                </div>
              ))}
          </div>
        </div>
      </WorkspaceContext.Provider>
    </ToastContext.Provider>
  );
}

createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <App />
  </React.StrictMode>,
);
