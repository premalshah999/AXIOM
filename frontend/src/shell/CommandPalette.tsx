import { CornerDownLeft, Link2, Palette, Search } from "lucide-react";
import { useEffect, useId, useMemo, useRef, useState } from "react";
import { api } from "../api";
import { useWorkspace, type Route } from "../context";
import { OPEN_JOB_LINK } from "../jobintake";
import { STATES, stateInfo } from "../model";
import type { Listing, PipelinePage } from "../types";
import { ALL_ITEMS } from "./nav";
import { THEMES, useTheme } from "./prefs";

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
 * Command palette (⌘K or Ctrl-K): jump to any page, add a job link, switch theme, or search jobs by company or title.
 * Jobs come from the same endpoints the Jobs view pages through (GET /api/jobs for the pipeline and GET /api/job-board
 * for discovered listings); choosing one opens its panel over the current page.
 */
export function CommandPalette({ open, onClose }: { open: boolean; onClose: () => void }) {
  const { go, openJob } = useWorkspace();
  const [, setTheme] = useTheme();
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
      note: i.route.view === "setup" ? "Setup" : i.soon ? "Coming soon" : undefined,
      icon: <i.icon size={15} aria-hidden="true" />,
      run: done(() => go(i.route as Route)),
    }));
    const actions: Entry[] = [
      {
        id: "act:link",
        group: "Actions",
        label: "Add a job link",
        icon: <Link2 size={15} aria-hidden="true" />,
        run: done(() => window.dispatchEvent(new Event(OPEN_JOB_LINK))),
      },
      ...THEMES.map<Entry>((t) => ({
        id: "theme:" + t.id,
        group: "Actions",
        label: "Theme: " + t.label,
        note: t.note,
        icon: <Palette size={15} aria-hidden="true" />,
        run: done(() => setTheme(t.id)),
      })),
    ];
    const needle = term.toLowerCase();
    const match = (e: Entry) => !needle || (e.label + " " + (e.note || "")).toLowerCase().includes(needle);
    const jobs: Entry[] = hits.map((h) => ({
      id: "job:" + h.id,
      group: "Jobs",
      label: h.company + (h.title ? " · " + h.title : ""),
      note: stateInfo(h.status in STATES ? h.status : "job:" + h.status).label,
      run: done(() => openJob(h.id)),
    }));
    // With no query, offer pages first and only the useful actions; with a query, pages then jobs then actions.
    return [...pages.filter(match), ...jobs, ...actions.filter((a) => (needle ? match(a) : !a.id.startsWith("theme:")))];
  }, [term, hits, go, openJob, onClose, setTheme]);

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
                      {on && <CornerDownLeft size={13} aria-hidden="true" />}
                    </li>
                  );
                })}
              </ul>
            </li>
          ))}
          {term.length >= 2 && searching && hits.length === 0 && <li className="ax-palette-empty" role="presentation">Searching jobs…</li>}
          {term.length >= 2 && !searching && failed && <li className="ax-palette-empty" role="presentation">Could not search jobs. Pages still work.</li>}
          {term.length >= 2 && !searching && !failed && flat.length === 0 && <li className="ax-palette-empty" role="presentation">No match for “{term}”.</li>}
          {term.length === 1 && flat.length === 0 && <li className="ax-palette-empty" role="presentation">No page matches. Keep typing to search jobs.</li>}
        </ul>
        <div className="ax-palette-foot">
          <span>↑↓ to move</span>
          <span>↵ to open</span>
          <span>Jobs: type 2+ letters of a company or title</span>
        </div>
      </div>
    </div>
  );
}
