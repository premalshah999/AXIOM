// Whether the owner chose "Finish later" in the setup guide. A module of its own so the dashboard shell can read it
// without loading the whole guide, which now loads only when shown (2026-10-23: a lighter first paint).
// "Finish later" holds for this browser: the app opens with a Continue setup banner instead of the guide.
const GUIDE_LATER = "applypilot:guide-later";

export function guideLater(value?: boolean) {
  try {
    if (value === true) localStorage.setItem(GUIDE_LATER, "1");
    else if (value === false) localStorage.removeItem(GUIDE_LATER);
    return localStorage.getItem(GUIDE_LATER) === "1";
  } catch {
    return false;
  }
}
