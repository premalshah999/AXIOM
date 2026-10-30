import { Link2, Moon, Search, Sun } from "lucide-react";
import { useEffect, useId, useMemo, useRef, useState } from "react";
import { api } from "../api";
import { useWorkspace } from "../context";
import { OPEN_JOB_LINK } from "../jobintake";
import { STATES, stateInfo } from "../model";
import type { Listing, PipelinePage } from "../types";
import { ALL_ITEMS, PAGE_TABS } from "./nav";
import { useTheme } from "./prefs";

type Entry = {
  id: string;
  group: "Pages" | "Jobs" | "Actions";
  label: string;
  note?: string;
  icon?: React.ReactNode;
  run: () => void;
};

type Hit = { id: string; company: string; title: string; status: string };

const GROUP_ORDER = ["Pages", "Jobs", "Actions"] as const;

/**
 * Command palette (⌘K or Ctrl-K): jump to any page or tab, add a job link, switch theme, or search jobs by company or title.
 * Jobs come from the same endpoints the Jobs view pages through (GET /api/jobs for the pipeline and GET /api/job-board
 * for discovered listings); choosing one opens its panel over the current page.
 */
export function CommandPalette({ open, onClose }: { open: boolean; onClose: () => void }) {
  const { go, openJob } = useWorkspace();
  const [theme, toggleTheme] = useTheme();
  const [q, setQ] = useState("");
  const [cursor, setCursor] = useState(0);
  const [hits, setHits] = useState<Hit[]>([]);
  const [searching, setSearching] = useState(false);
  const [failed, setFailed] = useState(false);
  const input = useRef<HTMLInputElement>(null);
  const list = useRef<HTMLUListElement>(null);
  const listId = useId();

  useEffect(() => {
    if (!open) return;
    setQ("");
    setCursor(0);
    setHits([]);
    setFailed(false);
    const opener = document.activeElement as HTMLElement | null;
    return () => opener?.focus?.();
  }, [open]);

  // Job search: wait for typing to settle, ask both lists, show the first rows of each.
  const term = q.trim();
  useEffect(() => {
    if (!open || term.length < 2) {
      setHits([]);
      setSearching(false);
      setFailed(false);
      return;
    }
    const controller = new AbortController();
    setSearching(true);
    const timer = window.setTimeout(async () => {
      const enc = encodeURIComponent(term);
      const [pipe, board] = await Promise.allSettled([
        api<PipelinePage>(`/jobs?group=all&q=${enc}&page=1&size=6`, "GET", undefined, controller.signal),
        api<{ jobs: Listing[] }>(
          `/job-board?include_sources=false&q=${enc}&ats=all&level=all&fit=all&page=1&size=6`,
          "GET",
          undefined,
          controller.signal,
        ),
      ]);
      if (controller.signal.aborted) return;
      const seen = new Set<string>();
      const out: Hit[] = [];
      const add = (id: string, company: string, title: string, status: string) => {
        if (seen.has(id)) return;
        seen.add(id);
        out.push({ id, company, title, status });
      };
      if (pipe.status === "fulfilled") for (const r of pipe.value.rows) add(r.job.id, r.job.company, r.job.title, r.run?.state || r.job.status);
      if (board.status === "fulfilled") for (const j of board.value.jobs || []) add(j.id, j.company, j.title, j.status);
      setHits(out.slice(0, 8));
      setFailed(pipe.status === "rejected" && board.status === "rejected");
      setSearching(false);
    }, 220);
    return () => {
      controller.abort();
      window.clearTimeout(timer);
    };
  }, [open, term]);

  const entries = useMemo<Entry[]>(() => {
    const done = (fn: () => void) => () => {
      onClose();
      fn();
    };
    const pages: Entry[] = ALL_ITEMS.map((i) => ({
      id: "page:" + i.id,
      group: "Pages",
      label: i.label,
      note: i.soon ? "Coming soon" : undefined,
      icon: <i.icon size={15} aria-hidden="true" />,
      run: done(() => go(i.route)),
    }));
    // A page's tabs ("Discovered", "Accounts") are places to jump to as well, once something is typed.
    const tabs: Entry[] = ALL_ITEMS.flatMap((i) =>
      (PAGE_TABS[i.route.view] || []).map<Entry>((t) => ({
        id: `tab:${i.id}:${t.id}`,
        group: "Pages",
        label: t.label,
        note: i.label,
        icon: <i.icon size={15} aria-hidden="true" />,
        run: done(() => go({ view: i.route.view, tab: t.id })),
      })),
    );
    const link: Entry = {
      id: "act:link",
      group: "Actions",
      label: "Add a job link",
      icon: <Link2 size={15} aria-hidden="true" />,
      run: done(() => window.dispatchEvent(new Event(OPEN_JOB_LINK))),
    };
    const switchTheme: Entry = {
      id: "act:theme",
      group: "Actions",
      label: theme === "dark" ? "Switch to light theme" : "Switch to dark theme",
      icon: theme === "dark" ? <Sun size={15} aria-hidden="true" /> : <Moon size={15} aria-hidden="true" />,
      run: done(toggleTheme),
    };
    const needle = term.toLowerCase();
    const match = (e: Entry) => !needle || (e.label + " " + (e.note || "")).toLowerCase().includes(needle);
    const jobs: Entry[] = hits.map((h) => ({
      id: "job:" + h.id,
      group: "Jobs",
      label: h.company + (h.title ? " · " + h.title : ""),
      note: stateInfo(h.status in STATES ? h.status : "job:" + h.status).label,
      run: done(() => openJob(h.id)),
    }));
    // With no query, offer the pages and adding a link; with a query, every match: pages and tabs, then jobs, then actions.
    if (!needle) return [...pages, link];
    return [...pages.filter(match), ...tabs.filter(match), ...jobs, ...[link, switchTheme].filter(match)];
  }, [term, hits, go, openJob, onClose, theme, toggleTheme]);

  useEffect(() => setCursor(0), [term, hits.length]);
  useEffect(() => {
    list.current?.querySelector('[aria-selected="true"]')?.scrollIntoView({ block: "nearest" });
  }, [cursor]);

  if (!open) return null;

  const groups = GROUP_ORDER.map((g) => ({ g, items: entries.filter((e) => e.group === g) })).filter((x) => x.items.length);
  const flat = groups.flatMap((x) => x.items);
  const active = flat[Math.min(cursor, flat.length - 1)];

  return (
    <div className="ax-palette-root" onMouseDown={(e) => e.target === e.currentTarget && onClose()}>
      <div className="ax-palette" role="dialog" aria-modal="true" aria-label="Command palette">
        <div className="ax-palette-input">
          <Search size={16} aria-hidden="true" />
          <input
            ref={input}
            autoFocus
            value={q}
            onChange={(e) => setQ(e.target.value)}
            placeholder="Search pages and jobs"
            role="combobox"
            aria-expanded="true"
            aria-controls={listId}
            aria-activedescendant={active ? listId + "-" + active.id : undefined}
            aria-label="Search pages and jobs"
            autoComplete="off"
            spellCheck={false}
            onKeyDown={(e) => {
              if (e.key === "Escape") {
                e.preventDefault();
                onClose();
              } else if (e.key === "ArrowDown") {
                e.preventDefault();
                setCursor((c) => Math.min(c + 1, flat.length - 1));
              } else if (e.key === "ArrowUp") {
                e.preventDefault();
                setCursor((c) => Math.max(c - 1, 0));
              } else if (e.key === "Enter" && active) {
                e.preventDefault();
                active.run();
              } else if (e.key === "Tab") {
                e.preventDefault();
              }
            }}
          />
          <kbd>esc</kbd>
        </div>
        <ul ref={list} id={listId} role="listbox" className="ax-palette-list" aria-label="Results">
          {groups.map(({ g, items }) => (
            <li key={g} role="presentation">
              <p className="ax-palette-group">{g}</p>
              <ul role="presentation">
                {items.map((e) => {
                  const on = e === active;
                  return (
                    <li
                      key={e.id}
                      id={listId + "-" + e.id}
                      role="option"
                      aria-selected={on}
                      className={"ax-palette-item" + (on ? " is-on" : "")}
                      onMouseMove={() => setCursor(flat.indexOf(e))}
                      onClick={e.run}
                    >
                      {e.icon}
                      <span className="ax-palette-label">{e.label}</span>
                      {e.note && <span className="ax-palette-note">{e.note}</span>}
                    </li>
                  );
                })}
              </ul>
            </li>
          ))}
          {term.length >= 2 && searching && hits.length === 0 && <li className="ax-palette-empty" role="presentation">Searching jobs…</li>}
          {term.length >= 2 && !searching && failed && <li className="ax-palette-empty" role="presentation">Could not search jobs. Pages still work.</li>}
          {term.length >= 2 && !searching && !failed && flat.length === 0 && <li className="ax-palette-empty" role="presentation">No match for “{term}”.</li>}
          {term.length === 1 && flat.length === 0 && <li className="ax-palette-empty" role="presentation">Keep typing to search jobs.</li>}
        </ul>
      </div>
    </div>
  );
}
