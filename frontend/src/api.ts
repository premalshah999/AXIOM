// Same-origin JSON client. The session lives in the HttpOnly jp_session cookie;
// no token is ever read, stored or sent from browser code.

export class ApiError extends Error {
  constructor(
    message: string,
    readonly status: number,
  ) {
    super(message);
  }
}

export const SIGNED_OUT = "applypilot:signed-out";
/** Announce that this browser is signed out; the sign-in screen shows `reason` (CustomEvent detail). */
export function signedOut(reason = "") {
  window.dispatchEvent(new CustomEvent(SIGNED_OUT, { detail: reason }));
}
// A 401 from these is a refused credential on the sign-in screen, not a session that ended.
const CREDENTIAL_PATHS = new Set(["/login", "/logout", "/account", "/account/login", "/account/reset"]);

function detail(data: unknown, status: number): string {
  const value = (data as { detail?: unknown } | null)?.detail;
  if (typeof value === "string" && value) return value;
  // FastAPI validation errors: [{loc: [...], msg}]
  if (Array.isArray(value) && value.length)
    return value
      .map((item) => {
        const loc = Array.isArray(item?.loc)
          ? item.loc.filter((x: unknown) => x !== "body").join(" › ")
          : "";
        return (loc ? loc + ": " : "") + (item?.msg || "Invalid value");
      })
      .join("; ");
  if (status === 404) return "Not found. It may have been removed.";
  if (status >= 500) return "AXIOM hit a server error. Try again shortly.";
  return "Request failed (" + status + ")";
}

export async function api<T = any>(
  path: string,
  method = "GET",
  body?: unknown,
  signal?: AbortSignal,
): Promise<T> {
  const form = body instanceof FormData;
  const writes = method === "POST" || method === "PUT" || method === "PATCH";
  let response: Response;
  try {
    response = await fetch("/api" + path, {
      method,
      signal,
      credentials: "same-origin",
      headers: writes && !form ? { "Content-Type": "application/json" } : {},
      // The API middleware rejects body-less writes; an empty POST sends {}.
      body: writes
        ? form
          ? (body as FormData)
          : JSON.stringify(body ?? {})
        : undefined,
    });
  } catch (error) {
    if ((error as Error).name === "AbortError") throw error;
    throw new ApiError(
      "Can't reach AXIOM. Check your connection, or that the app is running.",
      0,
    );
  }
  const data = await response.json().catch(() => null);
  if (!response.ok) {
    const message = detail(data, response.status);
    if (response.status === 401 && !CREDENTIAL_PATHS.has(path)) signedOut(message);
    throw new ApiError(message, response.status);
  }
  return data as T;
}

export type Api = typeof api;

// Queue admission defers a job while its tailored resume is prepared. That is
// automatic preparation, not a failure; callers show it as progress.
export const PREPARING =
  "Preparing a tailored resume first. The application starts on its own when it's ready.";
export function isPreparing(error: unknown) {
  return error instanceof ApiError && /preparing a tailored resume/i.test(error.message);
}

export type QueueResult = { preparing: boolean; deferred?: boolean; message?: string };

export async function queueJob(path: string, body: unknown): Promise<QueueResult> {
  try {
    const data = await api<{ preparation?: unknown; preparing?: boolean; deferred?: boolean; message?: string }>(
      path,
      "POST",
      body,
    );
    return {
      preparing: !!(data?.preparation || data?.preparing),
      deferred: !!data?.deferred,
      message: data?.message,
    };
  } catch (error) {
    if (isPreparing(error)) return { preparing: true };
    throw error;
  }
}

// One click applies: the server approves the job (no reason needed) and starts it, or keeps the request until
// workers, pacing or a shared email sender allow it. Waiting is progress, not an error.
export type ApplyResult = {
  job_id: string;
  state: string;
  message: string;
  run_id?: string;
  deferred?: boolean;
  preparing?: boolean;
};

export function applyJob(jobId: string): Promise<ApplyResult> {
  return api<ApplyResult>(`/jobs/${jobId}/apply`, "POST");
}

// A reserved employer (FAANG, MANGO, the Big Four) applies only with the owner's approval of the job (2026-10-27).
// `apply` (the default) also starts it, exactly as Apply does; false only lifts the hold.
export type ApproveResult = ApplyResult & { approved: boolean; employer: string; label: string };

export function approveReserved(jobId: string, apply = true): Promise<ApproveResult> {
  return api<ApproveResult>(`/jobs/${jobId}/approve-reserved`, "POST", { apply });
}
