// What a resume read gives the setup guide's boxes. No screen here, so tests/test_setup_resume_read.py runs it in node.
import type { Profile as Draft } from "./types";

// A resume writes its phone with the country code ("+1 (555) 010-0100"), and the Contact step has a box for the code: left in the
// number, applications would send it twice. A calling code is one digit (1, 7), two (these ranges) or three.
const callingCode = (digits: string) =>
  /^[17]/.test(digits) ? digits.slice(0, 1) : /^(2[07]|3[0-469]|4[013-9]|5[1-8]|6[0-6]|8[1246]|9[0-58])/.test(digits) ? digits.slice(0, 2) : digits.slice(0, 3);

export function splitPhone(raw: string) {
  const text = raw.trim().replace(/^\(\s*(\+\s*\d{1,3})\s*\)/, "$1");
  const digits = text.replace(/\D/g, "");
  // "1 (555) 010-0100" is a US number written with its leading 1.
  const code = text.startsWith("+") ? callingCode(digits) : digits.length === 11 && digits.startsWith("1") ? "1" : "";
  if (!code) return { phone: text, code: "" };
  let at = 0;
  for (let seen = 0; seen < code.length; at++) if (/\d/.test(text[at]!)) seen++;
  return { phone: text.slice(at).replace(/^[\s.\-)/]+/, ""), code: "+" + code };
}

const US_STATE_NAMES = new Set(
  "alabama|alaska|arizona|arkansas|california|colorado|connecticut|delaware|district of columbia|florida|georgia|hawaii|idaho|illinois|indiana|iowa|kansas|kentucky|louisiana|maine|maryland|massachusetts|michigan|minnesota|mississippi|missouri|montana|nebraska|nevada|new hampshire|new jersey|new mexico|new york|north carolina|north dakota|ohio|oklahoma|oregon|pennsylvania|rhode island|south carolina|south dakota|tennessee|texas|utah|vermont|virginia|washington|west virginia|wisconsin|wyoming".split("|"),
);
const US_STATE_CODES = new Set("AL AK AZ AR CA CO CT DC DE FL GA HI ID IL IN IA KS KY LA ME MD MA MI MN MS MO MT NE NV NH NJ NM NY NC ND OH OK OR PA RI SC SD TN TX UT VT VA WA WV WI WY".split(" "));

/** "Austin, TX", "Austin, Texas, USA", "Austin TX 78701": the Contact step asks for city and state. Anywhere else stays for the person to type. */
export function cityState(location: string) {
  const text = location.replace(/\(.*?\)/g, " ").trim();
  // Without a comma, only a state code can say where the city ends.
  const parts = text.includes(",") ? text.split(",").map((part) => part.trim()) : (/^(.+?)\s+([A-Za-z]{2}(?:\s+\d{5}(?:-\d{4})?)?)$/.exec(text)?.slice(1) ?? []);
  for (let i = 1; i < parts.length; i++) {
    const m = /^(.*?)(?:\s+(\d{5}(?:-\d{4})?))?$/.exec(parts[i]!)!;
    const state = m[1]!.replace(/\./g, "").trim();
    const code = state.toUpperCase();
    if (parts[i - 1] && (US_STATE_CODES.has(code) || US_STATE_NAMES.has(state.toLowerCase())))
      return { city: parts[i - 1]!, state: US_STATE_CODES.has(code) ? code : state, postal_code: m[2] || "" };
  }
  return null;
}

// What a resume read adds beyond the profile's fields. The notice says when a long resume was read only in part.
export type Extract = Partial<Draft> & { projects?: { name: string; description: string }[]; notice?: string };

/** A resume's projects as the notes Profile shows under Work: "Project: name", then its bullets, which is how tailoring reads them (evidence.py). */
const projectNotes = (x: Extract | null) =>
  (x?.projects ?? []).filter((p) => p.name?.trim()).map((p) => `Project: ${p.name.trim()}\n${(p.description || "").trim()}`.trim());

const asRead = (x: Partial<Draft>) => ({ ...x, ...splitPhone(x.phone || ""), place: cityState(x.location || "") });
const sameEntry = (a: object, b: object) => JSON.stringify(Object.entries(a).sort()) === JSON.stringify(Object.entries(b).sort());
/** Nothing in this list but what the last resume read put there (some may have been removed since), so a new read may replace it. */
export const onlyFromRead = <T extends object>(list: T[], read?: T[]) => list.every((entry) => (read ?? []).some((other) => sameEntry(entry, other)));

/**
 * A box or list takes the new resume's value when it is empty or still holds what the last read put there; what the person typed stays.
 * `last` is the server's memory of that read (POST /profile/extract), so choosing another resume replaces the first one's jobs and
 * schools rather than leaving them under a message saying the new ones were found, also after the guide was closed and reopened.
 */
export function mergeExtract(draft: Draft, raw: Extract, last: Extract | null): Draft {
  const x = asRead(raw);
  const before = last ? asRead(last) : null;
  const ours = (now: unknown, was: unknown) => !String(now ?? "").trim() || (!!before && String(now ?? "") === String(was ?? ""));
  const next = { ...draft };
  // The same replace-unless-edited as work and schooling below.
  const stale = new Set(projectNotes(last));
  const keptNotes = (next.evidence || []).filter((e) => !stale.has(e.text));
  const used = new Set(keptNotes.map((e) => e.id));
  next.evidence = [...keptNotes];
  for (const text of projectNotes(raw).filter((note) => !keptNotes.some((e) => e.text === note))) {
    let n = 1;
    while (used.has(`resume-project-${n}`)) n += 1;
    used.add(`resume-project-${n}`);
    next.evidence.push({ id: `resume-project-${n}`, text });
  }
  for (const key of ["first_name", "last_name", "email", "phone", "location", "linkedin", "github", "website"] as const)
    if (x[key] && ours(next[key], before?.[key])) next[key] = x[key] as string;
  if (x.code && next.phone === x.phone) next.phone_country_code = x.code;
  if (onlyFromRead(next.work, before?.work)) next.work = raw.work ?? [];
  if (onlyFromRead(next.education, before?.education)) next.education = raw.education ?? [];
  if (raw.skills?.length) {
    const old = new Set(before?.skills ?? []);
    next.skills = Array.from(new Set([...(next.skills || []).filter((k) => !old.has(k)), ...raw.skills]));
  }
  const { city, state, postal_code } = next.address;
  if (x.location && ours(city, before?.place?.city) && ours(state, before?.place?.state))
    next.address = {
      ...next.address,
      city: x.place?.city ?? "",
      state: x.place?.state ?? "",
      postal_code: ours(postal_code, before?.place?.postal_code) ? (x.place?.postal_code ?? "") : postal_code,
    };
  if (ours(next.name, [before?.first_name, before?.last_name].filter(Boolean).join(" "))) next.name = [next.first_name, next.last_name].filter(Boolean).join(" ");
  return next;
}
