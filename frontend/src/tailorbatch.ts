// How a list asks for its rows' tailoring choices: once for the whole page, not once per row (the owner saw lag on the Job board and the review batch).
// No imports on purpose: tests/test_tailor_choices_batch.py runs this file under Node with a counting stand-in for the request.
export const CHOICES_PATH = "/v2/resumes/tailor/choices";
// The server reads at most 200 ids per call; a page is 25 to 100 rows, so a page is one call.
export const PER_REQUEST = 200;

export type Choices<S, M> = { choices: Record<string, S>; modes: M[] };

export async function loadChoices<S, M>(ids: string[], get: (path: string) => Promise<Choices<S, M>>): Promise<Choices<S, M>> {
  const unique = [...new Set(ids)];
  const out: Choices<S, M> = { choices: {}, modes: [] };
  for (let i = 0; i < unique.length; i += PER_REQUEST) {
    const r = await get(`${CHOICES_PATH}?job_ids=${unique.slice(i, i + PER_REQUEST).map(encodeURIComponent).join(",")}`);
    Object.assign(out.choices, r.choices);
    out.modes = r.modes?.length ? r.modes : out.modes;
  }
  return out;
}

/** A row on a list is told its state by the list. Only a control standing alone (the job page) asks for itself. */
export function rowFetchesItself(listed: boolean, known: unknown, modes: unknown): boolean {
  if (listed) return false;
  return known === undefined || !modes;
}

/** After a refetch of `asked` ids: the answer for those, every other row as it was. A row the server has no choice for is plain Apply. */
export function mergeChoices<S>(was: Record<string, S>, asked: string[], got: Record<string, S>): Record<string, S> {
  const next = { ...was };
  for (const id of asked) {
    if (got[id]) next[id] = got[id];
    else delete next[id];
  }
  return next;
}
