// Jobs › Pipeline pages from the server (GET /api/jobs): every job, exact totals, only the rows on screen.
// The dashboard used to page through the newest 1,000 jobs it had been sent, so older jobs and most counts were
// missing or wrong with 7,000+ jobs (2026-10-22).
import { useEffect, useMemo, useState } from "react";
import { api } from "./api";
import { useWorkspace } from "./context";
import { usePoll } from "./hooks";
import { rowFromServer, withMarks, type Group } from "./model";
import type { Paging } from "./pagination";
import type { PipelinePage } from "./types";

// Typing waits this long before asking the server.
const SEARCH_DELAY = 250;

export function usePipelinePage(
  group: Group | "all",
  query: string,
  initialSize = 25,
) {
  const { version, marks, counts } = useWorkspace();
  // The tab and chip counts come from the snapshot; the rows from this page. Re-read the page whenever the counts move,
  // so a job never shows in one state on the chips and another in the list (owner 2026-10-28: "not properly synced").
  const countsKey = JSON.stringify(counts.groups);
  const [page, setPage] = useState(1);
  const [size, setSize] = useState(initialSize);
  const [q, setQ] = useState(query.trim());
  useEffect(() => {
    const timer = window.setTimeout(() => setQ(query.trim()), SEARCH_DELAY);
    return () => window.clearTimeout(timer);
  }, [query]);
  useEffect(() => setPage(1), [group, q, size]);
  const params = new URLSearchParams({
    group,
    q,
    page: String(page),
    size: String(size),
  });
  const key = params.toString();
  const poll = usePoll<PipelinePage & { key: string }>(
    async (signal) => ({
      ...(await api<PipelinePage>(`/jobs?${key}`, "GET", undefined, signal)),
      key,
    }),
    8000,
    [key, version, countsKey],
  );
  const data = poll.data;
  const current = data?.key === key;
  const rows = useMemo(
    () =>
      withMarks((data?.rows || []).map(rowFromServer), marks, poll.requestedAt),
    [data, marks, poll.requestedAt],
  );
  const paging: Paging = {
    items: rows,
    total: data?.total ?? 0,
    page: data?.page ?? page,
    pages: data?.pages ?? 1,
    size,
    initialSize,
    setPage,
    setSize,
  };
  return {
    rows,
    paging,
    // Counts read with these rows (the snapshot's can lag); undefined until the first page arrives.
    counts: data?.counts,
    // False until the rows for the current filter, search and page have arrived.
    current,
    loaded: !!data,
    error: poll.error,
    refresh: poll.refresh,
  };
}
