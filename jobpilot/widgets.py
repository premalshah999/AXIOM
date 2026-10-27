"""Drivers for the controls real ATS forms use. Each commits an observed option and reads it back."""

import asyncio
import calendar
import html
import re
import time
from datetime import date, datetime

from playwright.async_api import Error as PlaywrightError
from playwright.async_api import TimeoutError as PlaywrightTimeoutError

from .playwright_fast import install as install_playwright_fast

# The drivers below call Locator.evaluate about a hundred times per form; see playwright_fast.
install_playwright_fast()

OPTION_SELECTOR = (
    "[role=option],[role=menuitem],[role=menuitemradio],[role=treeitem],.select2-results__option,"
    ".chosen-results li,.ui-menu-item,.pac-item,.oj-listbox-result,.dropdown-location,[data-automation-id=promptOption],"
    "[data-automation-id=menuItem],.cx-select__list-item[role=gridcell]"
)

VISIBLE_OPTIONS = r"""(args) => {
  const roots = [];
  const visit = r => { roots.push(r); for (const e of r.querySelectorAll('*')) if (e.shadowRoot) visit(e.shadowRoot); };
  visit(document);
  const scope = args.scope && roots.map(r => r.querySelector('#' + CSS.escape(args.scope))).find(Boolean);
  if (args.scope && !scope) return []; // A stale owner reference must never expose another menu.
  const inside = e => { for(let n=e; n; n=n.assignedSlot || n.parentElement || n.getRootNode()?.host) if(n===scope) return true; return false; };
  const elements = roots.flatMap(r => [...r.querySelectorAll(args.selector)]).filter(e => !scope || inside(e));
  const controls = roots.flatMap(r => [...r.querySelectorAll('[data-jp-id]')]);
  const owners = new Map();
  const content = n => {
    if(n.nodeType === Node.TEXT_NODE) return n.textContent || '';
    if(n.nodeType !== Node.ELEMENT_NODE) return '';
    const assigned = n.tagName === 'SLOT' ? n.assignedNodes({flatten:true}) : [];
    return [...(assigned.length ? assigned : n.shadowRoot ? n.shadowRoot.childNodes : n.childNodes)].map(content).join(' ');
  };
  const out = [], seen = new Set();
  for (const el of elements) {
    if (!el.getClientRects().length || getComputedStyle(el).visibility === 'hidden' || el.matches('.c-spl-tag')
        || el.closest('[data-automation-id="selectedItem"],[data-automation-id="DELETE_charm"]')
        || el.querySelector('[data-automation-id="selectedItem"],[data-automation-id="DELETE_charm"]')) continue;
    // Nested matches (option inside menuitem) would duplicate the same choice.
    if ([...seen].some(p => p.contains(el))) continue;
    seen.add(el);
    // Ashby's school results add the country and web domain beside the name ("University of X / United States /
    // x.edu"); the website commits the name alone, so a committed choice never matched its option (Quora 2026-10-24).
    const named = el.querySelector('[class*="SchoolResultName"]');
    let text = content(named || el).replace(/\s+/g, ' ').trim();
    // SmartRecruiters repeats a slotted label in its visual/accessibility wrappers.
    const words = text.split(' '), mid = words.length / 2;
    if (Number.isInteger(mid) && words.slice(0, mid).join(' ') === words.slice(mid).join(' ')) text = words.slice(0, mid).join(' ');
    if (!text) continue;
    el.dataset.jpOpt = el.dataset.jpOpt || ('o' + Math.random().toString(36).slice(2, 10));
    const popup = el.closest('[role=listbox],[role=menu]');
    if (popup?.id && !owners.has(popup.id)) owners.set(popup.id, controls.filter(control => {
      const refs = (control.getAttribute('aria-controls') || control.getAttribute('aria-owns') || '').split(/\s+/);
      return refs.includes(popup.id) && control.getClientRects().length;
    }));
    const candidates = owners.get(popup?.id) || [];
    // Only explicit, unique DOM ownership may repair an omitted field ID.
    // A global open menu does not establish which applicant question owns it.
    out.push({id: el.dataset.jpOpt, text, scope: args.scope || null, selector: args.selector,
      owner_dom_id: candidates.length === 1 ? candidates[0].dataset.jpId : null,
      disabled: el.getAttribute('aria-disabled') === 'true',
      leaf: !el.querySelector('[data-automation-id=promptIcon],[aria-haspopup],[data-uxi-multiselectlistitem-hasChildren=true],svg[class*="chevron-right"]')
        && !el.matches('[data-uxi-multiselectlistitem-hasChildren=true]')
        && !['true','listbox','menu','tree'].includes(el.getAttribute('aria-haspopup'))
        && el.dataset.hasChildren !== 'true'});
  }
  return out;
}"""

US_STATES = {
    "AL": "Alabama",
    "AK": "Alaska",
    "AZ": "Arizona",
    "AR": "Arkansas",
    "CA": "California",
    "CO": "Colorado",
    "CT": "Connecticut",
    "DE": "Delaware",
    "DC": "District of Columbia",
    "FL": "Florida",
    "GA": "Georgia",
    "HI": "Hawaii",
    "ID": "Idaho",
    "IL": "Illinois",
    "IN": "Indiana",
    "IA": "Iowa",
    "KS": "Kansas",
    "KY": "Kentucky",
    "LA": "Louisiana",
    "ME": "Maine",
    "MD": "Maryland",
    "MA": "Massachusetts",
    "MI": "Michigan",
    "MN": "Minnesota",
    "MS": "Mississippi",
    "MO": "Missouri",
    "MT": "Montana",
    "NE": "Nebraska",
    "NV": "Nevada",
    "NH": "New Hampshire",
    "NJ": "New Jersey",
    "NM": "New Mexico",
    "NY": "New York",
    "NC": "North Carolina",
    "ND": "North Dakota",
    "OH": "Ohio",
    "OK": "Oklahoma",
    "OR": "Oregon",
    "PA": "Pennsylvania",
    "RI": "Rhode Island",
    "SC": "South Carolina",
    "SD": "South Dakota",
    "TN": "Tennessee",
    "TX": "Texas",
    "UT": "Utah",
    "VT": "Vermont",
    "VA": "Virginia",
    "WA": "Washington",
    "WV": "West Virginia",
    "WI": "Wisconsin",
    "WY": "Wyoming",
    "PR": "Puerto Rico",
}
DEGREE_LEVELS = [
    {"bachelor s degree", "bachelors", "bachelor", "bachelor s", "bachelors degree", "undergraduate degree"},
    {"master s degree", "masters", "master", "master s", "masters degree", "graduate degree"},
]
DEGREE_TYPES = [
    (0, {"ba", "b a", "bachelor of arts"}),
    (0, {"bs", "b s", "bsc", "b sc", "bachelor of science"}),
    (0, {"be", "b e", "bachelor of engineering"}),
    (0, {"btech", "b tech", "bachelor of technology"}),
    (1, {"ma", "m a", "master of arts"}),
    (1, {"ms", "m s", "msc", "m sc", "master of science"}),
    (1, {"mba", "master of business administration"}),
    (1, {"meng", "m eng", "master of engineering"}),
    (1, {"mtech", "m tech", "master of technology"}),
]
ALIASES = [
    # Localized career sites name it in their own language (SmartRecruiters' German "Vereinigte Staaten + 1",
    # Sopra Steria 2026-10-25).
    {"united states", "united states of america", "usa", "us", "u s", "u s a", "america", "vereinigte staaten",
     "vereinigte staaten von amerika", "etats unis", "états unis", "etats unis d amerique", "états unis d amérique",
     "estados unidos", "estados unidos de america", "estados unidos de américa", "stati uniti",
     "stati uniti d america", "verenigde staten"},
    {"united kingdom", "uk", "u k", "great britain", "england"},
    {"yes", "y", "true"},
    {"no", "n", "false"},
    {"mobile", "cell", "cell phone", "mobile phone", "cellular"},
    *DEGREE_LEVELS,
    *(aliases for _, aliases in DEGREE_TYPES),
    {"doctorate", "phd", "ph d", "doctor of philosophy", "doctoral degree", "doctorate degree"},
    {"associate s degree", "associates", "associate", "associate s"},
    {"high school diploma", "high school", "ged", "high school ged"},
]
for _code, _name in US_STATES.items():
    ALIASES.append({_code.lower(), _name.lower()})


def norm(text):
    return re.sub(r"\s+", " ", re.sub(r"[^\w\s+]", " ", str(text).lower())).strip()


def offered_degree_equivalent(field, expected):
    """An accepted same-level catalog fallback when the awarded type is absent."""

    def level(value):
        text = norm(re.sub(r"\([^)]*\)", "", str(value)))
        return next(
            (kind for kind, aliases in DEGREE_TYPES if text in aliases),
            next((kind for kind, aliases in enumerate(DEGREE_LEVELS) if text in aliases), None),
        )

    actual = field.get("value")
    options = field.get("options") or []
    return (
        bool(field.get("valid", True))
        and actual in options
        and level(actual) is not None
        and level(actual) == level(expected)
        and best_option(options, expected) is None
    )


def _alias_group(text):
    n = norm(text)
    for group in ALIASES:
        if n in group:
            return group
    return {n}


def placeholder_option(value):
    """Unselected picker prompts are not applicant answers ("None" can be)."""
    return bool(
        re.fullmatch(
            r"no selection|make a selection|(?:please )?(?:select|choose)(?: an?| one| your)?(?: option| answer| value)?",
            norm(value),
        )
    )


# A picker's own fallback choice. Saved from one catalog ("Other - Field of Study Not Listed"), it answers only a picker
# that offers it: typed into Oracle's free-text Major it replaced the applicant's field of study, and the pre-submit
# audit then held seven applications on the mismatch (2026-10-25).
CATALOG_FALLBACK = re.compile(r"^\W*(?:other|none of the above)\b|\bnot (?:listed|in (?:the |this )?list)\b", re.I)


def foreign_fallback(answer, saved_options, options):
    """A fallback chosen from a saved catalog, asked by a field whose options do not include it (or that has none)."""
    return (
        bool(saved_options)
        and isinstance(answer, str)
        and bool(CATALOG_FALLBACK.search(answer))
        and not any(norm(o) == norm(answer) for o in options or [])
    )


def money_amount(text):
    """Exact amount of a single plain/currency number such as "$85,000" or "85K"; else None."""
    match = re.fullmatch(
        r"\s*(?:USD\s*)?\$?\s*(\d{1,3}(?:,\d{3})+|\d+)(?:\.0+)?\s*([kK])?\s*(?:USD)?\s*", str(text)
    )
    if not match:
        return None
    return int(match[1].replace(",", "")) * (1000 if match[2] else 1)


def money_range(text):
    """(low, high) for "$80,000 - $100,000", "$300,000+" or "$300,000 or higher"; else None."""
    num = r"\$?\s*(\d{1,3}(?:,\d{3})+|\d+)\s*([kK])?"
    as_int = lambda n, k: int(n.replace(",", "")) * (1000 if k else 1)  # noqa: E731
    if match := re.fullmatch(r"\s*" + num + r"\s*(?:-|\u2013|to)\s*" + num + r"\s*", str(text)):
        low, high = as_int(match[1], match[2]), as_int(match[3], match[4])
        if not match[2] and match[4] and low < 1000 <= high:
            low *= 1000  # "26-30k" is 26k to 30k: the suffix belongs to both numbers.
        return low, high
    if match := re.fullmatch(r"\s*(?:less than|under|up to|below)\s*" + num + r"\s*", str(text), re.I):
        return 0, as_int(match[1], match[2])
    if match := re.fullmatch(r"\s*" + num + r"\s*(?:\+|or (?:higher|more|above))\s*", str(text), re.I):
        return as_int(match[1], match[2]), float("inf")
    return None


# "january" / "jan" / "sept" -> 1..12, for month menus that offer names.
MONTH_NUMBERS = {
    **{name.lower(): i for i, name in enumerate(calendar.month_name) if name},
    **{name.lower(): i for i, name in enumerate(calendar.month_abbr) if name},
    "sept": 9,
}


def best_option(options, value):
    """Choose one observed option for a desired value. Ambiguity returns None, never a guess."""
    options = [o for o in options if not placeholder_option(o["text"] if isinstance(o, dict) else o)]
    if not options:
        return None
    labels = [o["text"] if isinstance(o, dict) else o for o in options]
    want = norm(value)
    if not want:
        return None
    exact = [i for i, x in enumerate(labels) if norm(x) == want]
    if len(exact) >= 1:
        return options[exact[0]]
    # A month number in a menu of month names: Oracle's Start Date Month grid offers "January" ... "December", and an
    # education start of 2024-08 asked for "8" four times without a match (2026-10-25).
    if re.fullmatch(r"0?[1-9]|1[0-2]", want) and sum(norm(x) in MONTH_NUMBERS for x in labels) >= 6:
        named = [i for i, x in enumerate(labels) if MONTH_NUMBERS.get(norm(x)) == int(want)]
        if len(named) == 1:
            return options[named[0]]
    # "85000" is the offered "$85,000" (or "85K"). Only an exact single amount;
    # ranges and open-ended labels ("or higher") are left to interpretation.
    if (amount := money_amount(value)) is not None:
        same = [i for i, x in enumerate(labels) if money_amount(x) == amount]
        if len(same) == 1:
            return options[same[0]]
        # A bracket picker: the single range that contains the amount. An amount
        # on a shared boundary matches two ranges and stays unanswered.
        inside = [i for i, x in enumerate(labels) if (r := money_range(x)) and r[0] <= amount <= r[1]]
        if not same and len(inside) == 1:
            return options[inside[0]]
    # Some model answers add a short explanation to a binary choice. Workday
    # offers only the literal Yes/No labels, so commit the leading choice.
    binary = [i for i, x in enumerate(labels) if norm(x) in {"yes", "no"}]
    if {norm(labels[i]) for i in binary} == {"yes", "no"}:
        first = re.match(r"^(yes|no)\b(?:\s|[,.:;!?]|$)", str(value).strip(), re.I)
        if first:
            return options[next(i for i in binary if norm(labels[i]) == first[1].lower())]
    group = _alias_group(value)
    degree = next(((level, aliases) for level, aliases in DEGREE_TYPES if want in aliases), None)
    if degree or group in DEGREE_LEVELS:
        level, specialty = degree or (DEGREE_LEVELS.index(group), None)

        def mentions(label, aliases):
            return any(
                re.search(r"(?:^|\s)" + re.escape(alias) + r"(?:$|\s)", norm(label)) for alias in aliases
            )

        specific, generic = [], []
        for index, label in enumerate(labels):
            # ISCED headings describe a level; the parenthesized specialties
            # are examples of that category, not the applicant's awarded degree.
            category = re.fullmatch(r"(.+?)\s*\((.+)\)", label)
            if category and re.search(r"\bISCED\b|\be\.\s*g\.|\bexamples?\b", category[2], re.I):
                heading, examples = category.groups()
                code = re.search(r"\bISCED\s*([67])\s*:", examples, re.I)
                example_types = [
                    (kind, aliases) for kind, aliases in DEGREE_TYPES if mentions(examples, aliases)
                ]
                if (
                    (not code or int(code[1]) == level + 6)
                    and mentions(heading, DEGREE_LEVELS[level])
                    and not any(mentions(heading, aliases) for _, aliases in DEGREE_TYPES)
                    and len(example_types) >= 2
                    and all(kind == level for kind, _ in example_types)
                ):
                    generic.append(index)
                continue
            types = [aliases for _, aliases in DEGREE_TYPES if mentions(label, aliases)]
            if types:
                if specialty is not None and len(types) == 1 and types[0] == specialty:
                    specific.append(index)
            elif not re.search(r"\b(?:bachelor|master) of\b", norm(label)) and mentions(
                label, DEGREE_LEVELS[level]
            ):
                generic.append(index)
        # A degree can project to its generic level, never to another awarded specialty.
        matches = specific or generic
        return options[matches[0]] if len(matches) == 1 else None
    alias = [i for i, x in enumerate(labels) if norm(x) in group]
    if len(alias) == 1:
        return options[alias[0]]
    # A dial-code decoration does not make "Virgin Islands, U.S." an alias
    # for "USA". Match the whole country name before substring fallbacks.
    if group == ALIASES[0] or group == ALIASES[1]:
        decorated = [
            i for i, label in enumerate(labels) if norm(re.sub(r"\(?\+\s*\d{1,4}\)?", "", label)) in group
        ]
        return options[decorated[0]] if len(decorated) == 1 else None
    # "United States of America (+1)" for "+1"; "Mobile" for "Mobile Phone".
    contained = [
        i
        for i, x in enumerate(labels)
        if re.search(r"(^|\s)" + re.escape(want) + r"($|\s)", norm(x))
        or (norm(x) and re.search(r"(^|\s)" + re.escape(norm(x)) + r"($|\s)", want) and len(norm(x)) > 2)
    ]
    if len(contained) == 1:
        return options[contained[0]]
    if contained:
        if len(contained) > 1 and re.fullmatch(r"\+\d+", want):
            return None  # Multiple countries share a calling code; require applicant context.
        if len(contained) > 1 and any("," in labels[i] for i in contained):
            return None  # Geographic qualifiers need the applicant's address context.
        starts = [i for i in contained if norm(labels[i]).startswith(want)]
        if len(starts) == 1:
            return options[starts[0]]
        shortest = sorted(contained, key=lambda i: len(labels[i]))
        if len(norm(labels[shortest[0]])) < len(norm(labels[shortest[1]])) and norm(
            labels[shortest[0]]
        ).startswith(want):
            return options[shortest[0]]
    for alt in group - {want}:
        hits = [i for i, x in enumerate(labels) if re.search(r"(^|\s)" + re.escape(alt) + r"($|\s)", norm(x))]
        if len(hits) == 1:
            return options[hits[0]]
    return None


def phone_value(field, value, country_code=""):
    """Format a confirmed number for an observed separate calling-code input."""
    if field.get("phone_format") != "national":
        return value
    text = str(value)
    digits = re.sub(r"\D", "", text)
    code = re.sub(r"\D", "", field.get("phone_calling_code") or country_code)
    if (
        code
        and digits.startswith(code)
        and (text.strip().startswith("+") or (code == "1" and len(digits) == 11))
    ):
        digits = digits[len(code) :]
    if field.get("phone_display") == "us-hyphenated" and len(digits) == 10:
        return f"{digits[:3]}-{digits[3:6]}-{digits[6:]}"
    return digits


def checkbox_value(value):
    """Normalize an explicit checkbox state; prose must never silently uncheck it."""
    text = str(value).strip().casefold()
    if text in {"true", "yes", "checked", "1", "on"}:
        return "true"
    if text in {"false", "no", "unchecked", "0", "off"}:
        return "false"
    raise ValueError("Checkbox answer must specify checked or unchecked")


# A plain amount as typed or as a field formats it: "80000", "80,000", "$80,000.00", "80 000".
AMOUNT = re.compile(r"\$?\s*(\d{1,3}(?:[, \u00a0]\d{3})+|\d+)(?:\.(\d{1,2}))?")


def same_amount(a, b):
    """Two AMOUNT matches name the same number."""
    whole = [re.sub(r"\D", "", m[1]) for m in (a, b)]
    cents = [(m[2] or "").ljust(2, "0") for m in (a, b)]
    return whole[0] == whole[1] and cents[0] == cents[1]


def same_value(field, actual, expected):
    actual, expected = phone_value(field, actual), phone_value(field, expected)
    a, e = " ".join(str(actual).split()), " ".join(str(expected).split())
    if field.get("letter_case") == "upper" and a != a.upper():
        return False  # A capitals-only field still showing lower case was refused (Dallas County 2026-10-25).
    if a == e or (field.get("letter_case") == "upper" and a.upper() == e.upper()):
        return True
    from .forms import display_date

    if (shown := display_date(a)) and shown == display_date(e):
        return True  # "16 Oct 2026" (picker) is the date "10/16/2026" or "today".
    if (number := AMOUNT.fullmatch(a)) and (typed := AMOUNT.fullmatch(e)) and same_amount(number, typed):
        # Workable shows the typed salary "80000" as "80,000" (Funnel Leasing 2026-10-25: read back as refused).
        return True
    kind, label = field.get("type", ""), field.get("label", "")
    if kind == "tel" or re.search(r"phone|mobile|telephone|\bcell(?:ular)?\b", str(label), re.I):
        # "+1 555-555-0100" (widget format after typing) is the same number as "(555) 555-0100".
        da, de = re.sub(r"\D", "", a), re.sub(r"\D", "", e)
        if len(da) >= 10 and len(de) >= 10 and da[-10:] == de[-10:]:
            return True
    if kind == "checkbox":
        try:
            return checkbox_value(actual) == checkbox_value(expected)
        except ValueError:
            return False
    if field.get("provider_combo") == "oracle":
        from .widgets_ats import component_value, month_component

        if month_component(label):
            return norm(component_value(label, actual)) == norm(component_value(label, expected))
    # Some native/custom labels include their original placeholder. That
    # decoration must not disable phone-code readback (iCIMS does this).
    code_label = re.split(r"\s*[—–]\s*", label, maxsplit=1)
    if len(code_label) == 2 and placeholder_option(code_label[1]):
        label = code_label[0].strip()
    if (
        norm(label) == "country"
        and kind == "combobox"
        and re.fullmatch(r"phone|telephone|mobile(?: number)?", field.get("section", ""), re.I)
    ):
        label = "Country phone code"
    if re.fullmatch(
        r"(?:country(?:\s*(?:/|or)\s*(?:region|territory))?(?: phone)?|phone country) code"
        r"|l(?:ä|ae|a)ndercode|landesvorwahl|indicatif(?: du pays| pays)?|c[oó]digo de pa[ií]s|prefisso(?: internazionale)?",
        label,
        re.I,
    ):
        actual_code = re.search(r"\+\s*\d{1,4}\b", a)
        expected_code = re.search(r"\+\s*\d{1,4}\b", e)
        if actual_code and expected_code:
            actual_country = norm(re.sub(r"\+\s*\d{1,4}\b", "", a))
            expected_country = norm(re.sub(r"\+\s*\d{1,4}\b", "", e))
            if actual_country and expected_country and actual_country not in _alias_group(expected_country):
                return False  # A shared calling code does not make two countries equivalent.
            return re.sub(r"\s", "", actual_code[0]) == re.sub(r"\s", "", expected_code[0])
        if actual_code and norm(e) in _alias_group("United States"):
            actual_country = norm(re.sub(r"\+\s*\d{1,4}\b", "", a))
            return actual_code[0] == "+1" and actual_country in _alias_group("United States")
    if field.get("widget") == "date-text" and field.get("date_format") == "dmy":
        try:
            d, m, y = str(actual).split("/")
            return parse_date(f"{y}-{m}-{d}") == parse_date(expected)
        except (ValueError, TypeError):
            return False
    if kind in {"date", "date_parts"} or field.get("widget") == "date-text":
        try:
            if field.get("date_format") == "month-year":
                a_month, _, a_year = parse_date(actual)
                e_month, _, e_year = parse_date(expected)
                return (a_month, a_year) == (e_month, e_year)
            return parse_date(actual) == parse_date(expected)
        except ValueError:
            return False
    if kind in {"checkboxgroup", "select-multiple"} or field.get("widget") == "oracle-multiselect":
        return {norm(v) for v in str(actual).splitlines()} == {norm(v) for v in str(expected).splitlines()}
    if kind in {"dropdown", "pills", "combobox", "select", "radio", "radiogroup", "buttonchoice"}:
        if re.fullmatch(r"degree(?: type| level)?", norm(label)):
            return best_option([a], e) is not None
        if norm(a) in {"yes", "no"} and norm(a) == norm(best_option(["Yes", "No"], e) or ""):
            return True
        return norm(a) == norm(e) or (bool(norm(e)) and norm(a) in _alias_group(e))
    if kind == "prompt":
        return bool(norm(e)) and norm(e) in norm(a)
    if kind == "text" and re.search(r"\bdate\b", label, re.I):
        # A UI5 date picker shows a month-only fact ("12/2025") as its first day ("Dec 1, 2025").
        try:
            a_date, e_date = parse_date(a), parse_date(e)
        except ValueError:
            a_date = e_date = None
        if a_date and e_date and a_date[0] and (a_date[0], a_date[2]) == (e_date[0], e_date[2]):
            if not (a_date[1] and e_date[1]) or a_date[1] == e_date[1]:
                return True
    if kind in {"tel", "date_parts", "date"} or re.search(r"phone|mobile|telephone|date", label, re.I):
        digits_a, digits_e = re.sub(r"\D", "", a), re.sub(r"\D", "", e)
        return bool(digits_e) and (digits_a == digits_e or digits_a.endswith(digits_e))
    if kind == "url" or re.search(r"linkedin|website|url|github|portfolio", label, re.I):
        return a.rstrip("/").lower().removeprefix("https://").removeprefix("http://").removeprefix(
            "www."
        ) == e.rstrip("/").lower().removeprefix("https://").removeprefix("http://").removeprefix("www.")
    return False


def parse_date(value):
    """Return (month, day, year) strings; missing precision stays empty."""
    v = str(value).strip().lower()
    if v in {"today", "now"}:
        t = date.today()
        return f"{t.month:02d}", f"{t.day:02d}", f"{t.year:04d}"
    m = re.fullmatch(r"(\d{4})-(\d{1,2})(?:-(\d{1,2}))?", v)
    if m:
        return f"{int(m[2]):02d}", f"{int(m[3]):02d}" if m[3] else "", m[1]
    m = re.fullmatch(r"(\d{1,2})/(\d{1,2})/(\d{4})", v)
    if m:
        return f"{int(m[1]):02d}", f"{int(m[2]):02d}", m[3]
    # A full date with a time or a note after it ("2026-10-23T00:00:00", "10/23/2026 (today)"): Paylocity's "Available
    # to Start" raised here on every write and the run stalled (Lightology, Space Tango 2026-10-26).
    m = re.match(r"(\d{4})-(\d{1,2})-(\d{1,2})(?![\d-])", v) or re.match(r"(\d{1,2})/(\d{1,2})/(\d{4})(?![\d/])", v)
    if m:
        year_first = len(m[1]) == 4
        month, day, year = (m[2], m[3], m[1]) if year_first else (m[1], m[2], m[3])
        return f"{int(month):02d}", f"{int(day):02d}", year
    m = re.fullmatch(r"(\d{1,2})/(\d{4})", v)
    if m:
        return f"{int(m[1]):02d}", "", m[2]
    m = re.fullmatch(r"(\d{4})", v)
    if m:
        return "", "", m[1]
    # Reviewed facts can be natural prose ("21st May 2026"). Preserve its
    # supplied day; month-only dates must still retain their lower precision.
    full_date = re.sub(r"\b(\d{1,2})(?:st|nd|rd|th)\b", r"\1", v)
    full_date = re.sub(r"\s+", " ", full_date.replace(",", " ")).strip()
    for fmt in ("%d %B %Y", "%d %b %Y", "%B %d %Y", "%b %d %Y", "%d-%b-%Y", "%d-%B-%Y"):
        try:
            parsed = datetime.strptime(full_date, fmt)
            return f"{parsed.month:02d}", f"{parsed.day:02d}", str(parsed.year)
        except ValueError:
            pass
    for fmt in ("%B %Y", "%b %Y"):
        try:
            parsed = datetime.strptime(v, fmt)
            return f"{parsed.month:02d}", "", str(parsed.year)
        except ValueError:
            pass
    raise ValueError("Unsupported date format")


def date_text(field, value):
    month, day, year = parse_date(value)
    parts = {"month": month, "day": day, "year": year}
    kinds = [p["kind"] for p in field.get("parts", [])] or ["month", "day", "year"]
    if any(not parts[k] for k in kinds):
        raise ValueError("The applicant date does not have the precision this field requires")
    return "/".join(parts[k] for k in kinds)


async def visible_options(frame, scope=None, selector=OPTION_SELECTOR):
    try:
        return await frame.evaluate(VISIBLE_OPTIONS, {"scope": scope, "selector": selector})
    except Exception:
        return []


def interaction_failure(exc):
    """Classify the useful part of a Playwright log without retaining typed values."""
    text = str(exc).lower()
    for category, patterns in (
        ("covered", ("intercepts pointer events", "intercepted by")),
        ("disabled", ("not enabled", "element is disabled")),
        ("hidden", ("not visible", "outside of the viewport")),
        ("moving", ("not stable",)),
        (
            "stale",
            (
                "not attached",
                "detached",
                "no longer",
                "not available",
                "stale browser observation",
                "control is stale",
            ),
        ),
        ("unsupported_choice", ("option is not offered", "no matching option", "not commit")),
        ("date_precision", ("precision this field requires",)),
    ):
        if any(pattern in text for pattern in patterns):
            return category
    return (
        "deadline"
        if isinstance(exc, TimeoutError) or "timeout" in type(exc).__name__.lower()
        else "unclassified"
    )


# A react-select (Greenhouse, Ashby) shows its menu state as a notice inside the menu: "Loading..." while a search is
# in flight, "No options" when it found nothing. Only a visible notice that says nothing was found counts.
MENU_NOTICE_JS = r"""() => {
  const shown = n => n.getClientRects().length && getComputedStyle(n).visibility !== 'hidden';
  const notices = [...document.querySelectorAll('[class*="menu-notice"]')].filter(shown);
  if (notices.some(n => /loading/i.test(n.className + ' ' + n.textContent))) return 'loading';
  if (notices.some(n => /\bno (options|results|matches|items)\b|\bnot found\b|\bnothing found\b/i.test(n.textContent)))
    return 'none';
  // Workday's prompt says "No matches found" in its list header and renders no option at all (REA, 2026-10-25):
  // waiting the full timeout for an option that cannot come cost 4 s per empty search.
  const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
  for (let n = walker.nextNode(); n; n = walker.nextNode()) {
    if (/^\s*no (?:matches|results|items)(?: found)?\.?\s*$/i.test(n.textContent) && n.parentElement && shown(n.parentElement))
      return 'none';
  }
  return '';
}"""


async def menu_notice(frame):
    """'loading', 'none' (the open menu says nothing matched) or '' when no such notice is shown."""
    try:
        return await frame.evaluate(MENU_NOTICE_JS)
    except Exception:
        return ""


async def wait_options(
    frame,
    scope=None,
    timeout=3.0,
    selector=OPTION_SELECTOR,
    settle=0.15,
    query=None,
    owner=None,
    require_query_match=False,
    changed_from=None,
    accept=None,
    stable_for=None,
    empty_after=None,
):
    """Wait for a stable option list. `changed_from` is the list shown before a search: a settled list that
    replaced it is that search's result even when no option repeats the query text.

    accept(options) -> truthy ends the wait on a settled list the caller can already use (an option that names the
    query's place differently: "City, MD" against "City, Maryland, United States").
    stable_for: how long a changed list must stay unchanged to count as the search's result (default: two reads).
    empty_after: seconds a menu must keep saying "No options" before the wait gives up; a search that found nothing
    otherwise burned the whole timeout."""
    deadline = time.monotonic() + timeout
    last = []
    matches_query = not query
    unchanged_since = None
    empty_since = None
    while time.monotonic() < deadline:
        # The owned menu's id is stable while it is open; look it up again only when it was not found yet or
        # the last read came back empty (the menu may have been re-rendered).
        if owner is not None and (scope is None or not last):
            scope = await _popup_scope(owner) or scope
        opts = await visible_options(frame, scope, selector)
        matches_query = not query or any(norm(query) in norm(o["text"]) for o in opts)
        if query and not matches_query and not require_query_match:
            # A fixed catalog may say United States while the confirmed query
            # says United States of America. An exact supported alias is already
            # usable; waiting the full timeout cannot improve this observation.
            usable = [o for o in opts if not o.get("disabled")]
            matches_query = bool(best_option(usable, query)) or bool(accept and usable and accept(usable))
        texts = [o["text"] for o in opts]
        same = bool(opts) and texts == [o["text"] for o in last]
        if same and unchanged_since is None:
            unchanged_since = time.monotonic()
        elif not same:
            unchanged_since = None
        changed = changed_from is not None and texts != list(changed_from)
        if same and matches_query:
            return opts
        if same and changed:
            if stable_for is None or time.monotonic() - unchanged_since >= stable_for:
                return opts
        if empty_after is not None and not opts:
            if await menu_notice(frame) == "none":
                empty_since = empty_since or time.monotonic()
                if time.monotonic() - empty_since >= empty_after:
                    return []
            else:
                empty_since = None
        last = opts
        await asyncio.sleep(settle)
    return [] if require_query_match and query and not matches_query else last


async def _popup_scope(el):
    try:
        return await el.evaluate(
            """e => {
              const reference = e.getAttribute('aria-controls') || e.getAttribute('aria-owns') ||
                e.querySelector('[aria-controls]')?.getAttribute('aria-controls');
              const referenced = reference && (e.getRootNode().getElementById?.(reference) || document.getElementById(reference));
              if (referenced && referenced.getClientRects().length && getComputedStyle(referenced).visibility !== 'hidden') return reference;
              // iCIMS links the sibling container to its listbox, while the
              // actual trigger has no aria-controls attribute.
              if (e.matches('a[id$="_icimsDropdown"][role=combobox]')) {
                const menu = document.getElementById(e.id + '_ctnr');
                if (menu?.getClientRects().length && getComputedStyle(menu).visibility !== 'hidden') return menu.id;
              }
              // Workday prompt results are siblings of the search input. They
              // belong to this container even when no ARIA relationship exists.
              const prompt = e.closest('[data-automation-id="multiSelectContainer"]');
              if (prompt && [...prompt.querySelectorAll('[role=listbox]:not([data-automation-id="selectedItemList"]):not([aria-label="items selected"]),[role=menu],[data-automation-id=promptOption]')]
                  .some(n => !n.closest('[data-automation-id="selectedItem"],[data-automation-id="selectedItemList"],[aria-label="items selected"]')
                    && n.getClientRects().length && getComputedStyle(n).visibility !== 'hidden')) {
                if (!prompt.id) prompt.id = 'jp-prompt-' + Math.random().toString(36).slice(2,10);
                return prompt.id;
              }
              // Some Workday buttons omit ARIA ownership. Only the single
              // visible popup is usable after other pickers have been dismissed.
              const menus = [...document.querySelectorAll('[role=listbox]:not([data-automation-id="selectedItemList"]):not([aria-label="items selected"]),[role=menu]')]
                .filter(n => n.getClientRects().length && getComputedStyle(n).visibility !== 'hidden');
              if (menus.length !== 1) return reference || null;
              if (!menus[0].id) menus[0].id = 'jp-menu-' + Math.random().toString(36).slice(2,10);
              return menus[0].id;
            }"""
        )
    except Exception:
        return None


async def options_scroll(frame, opts):
    """Whether the observed options sit in a scrolling container (a window of a longer list)."""
    try:
        return await frame.locator(f'[data-jp-opt="{opts[0]["id"]}"]').evaluate(
            """e=>{let n=e.parentElement;for(let i=0;i<8&&n&&n.tagName!=='BODY'&&n.tagName!=='HTML';i++,n=n.parentElement){
              if(n.scrollHeight>n.clientHeight+4 && /auto|scroll/.test(getComputedStyle(n).overflowY))return true;
            }return false;}""",
            timeout=1000,
        )
    except Exception:
        return True  # Unknown: keep the long-list search.


async def scroll_for_option(frame, value, scope=None, steps=8):
    """Search a virtualized menu by scrolling its own observed container."""
    for _ in range(steps):
        opts = await visible_options(frame, scope)
        if choice := best_option([o for o in opts if not o["disabled"]], value):
            return choice
        if not opts:
            return None
        moved = await frame.locator(f'[data-jp-opt="{opts[0]["id"]}"]').evaluate(
            """e=>{let n=e.parentElement;for(let i=0;i<8&&n&&n.tagName!=='BODY'&&n.tagName!=='HTML';i++,n=n.parentElement){
              if(n.scrollHeight>n.clientHeight+4 && /auto|scroll/.test(getComputedStyle(n).overflowY)){
                const before=n.scrollTop;n.scrollTop+=Math.max(40,n.clientHeight*.8);return n.scrollTop>before;
              }}return false;}""",
            timeout=1000,
        )
        if not moved:
            return None
        await asyncio.sleep(0.12)
    opts = await visible_options(frame, scope)
    return best_option([o for o in opts if not o["disabled"]], value)


async def click_option(frame, option, owner=None):
    # React can replace a result node between observing and clicking. Rebind only
    # to the same unique offered choice; an index or old node ID is insufficient.
    for attempt in range(2):
        scope = option.get("scope")
        if owner is not None:
            scope = await _popup_scope(owner) or scope
        current = await visible_options(frame, scope, option.get("selector", OPTION_SELECTOR))
        matches = [
            o
            for o in current
            if o["text"] == option["text"] and o["leaf"] == option["leaf"] and not o["disabled"]
        ]
        selected = next((o for o in matches if o["id"] == option["id"]), None)
        if selected is None and len(matches) == 1:
            selected = matches[0]
        if selected is None:
            raise ValueError("The observed option is no longer uniquely available")
        try:
            target = frame.locator(f'[data-jp-opt="{selected["id"]}"]')
            # Workday's role=option wrapper has no click handler. Its observed
            # promptLeafNode owns both category navigation and selection.
            inner = target.locator('[data-automation-id="promptLeafNode"]').first
            if await inner.count() and await inner.is_visible():
                target = inner
            await field_click(target, timeout=1500)
            return
        except Exception:
            if attempt:
                raise


async def ensure_uncovered(el):
    # A modal dialog blocks everything outside it. A non-modal one blocks only the controls it covers, and a site's
    # chat widget never does: Qualtrics (Greenhouse) 2026-10-18 refused "Apply now" three runs in a row because its
    # chatbot panel is a role=dialog in the corner.
    blocked = await el.evaluate(r"""e => {
        const CHAT=/chat|\bbot\b|chatbot|assistant|messenger|intercom|drift|olivia|paradox|livechat|zendesk|zopim|freshchat|tidio|hubspot-messages/i;
        const name=d=>[d.id, typeof d.className==='string'?d.className:'', d.getAttribute('aria-label')||'',
          d.getAttribute('title')||'', d.getAttribute('data-testid')||''].join(' ');
        const r=e.getBoundingClientRect(), cx=r.left+r.width/2, cy=r.top+r.height/2;
        const covers=d=>{const b=d.getBoundingClientRect();return cx>=b.left&&cx<=b.right&&cy>=b.top&&cy<=b.bottom};
        const dialogs=[...e.ownerDocument.querySelectorAll('[aria-modal="true"],[role="dialog"]')]
          .filter(d=>{const b=d.getBoundingClientRect();return b.width&&b.height&&getComputedStyle(d).visibility!=='hidden'})
          .filter(d=>!CHAT.test(name(d)) && (d.getAttribute('aria-modal')==='true' || covers(d)));
        if(e.closest('[inert]'))return true;
        // Dropdown/menu options render in portals outside the modal that owns
        // their combobox (Microsoft sign-up country list). They are part of the
        // open control; the click-point ownership check still applies.
        if(e.closest('[role="listbox"],[role="option"],[role="menu"],[role="menuitem"],[role="tree"],[role="grid"]'))return false;
        return dialogs.length && !dialogs.some(d=>d.contains(e));
    }""")
    if blocked:
        raise ValueError("Control is covered by the active dialog or inert")


async def pointer_path(handle):
    """Move the pointer to an inner point of the target in a few steps, with no pause. SmartRecruiters (DataDome) and
    Greenhouse/Ashby (reCAPTCHA Enterprise v3) score pointer telemetry; the old sleeps were the slow part, not the
    movement. Returns the click position relative to the element, or None for Playwright's default."""
    try:
        frame = await handle.owner_frame()
        page = frame.page if frame else None
        box = await asyncio.wait_for(handle.bounding_box(), 1.0) if page else None
        if not box or box["width"] < 4 or box["height"] < 4:
            return None
        x, y = box["width"] * 0.45, box["height"] * 0.5
        await asyncio.wait_for(page.mouse.move(box["x"] + x, box["y"] + y, steps=5), 1.0)
        return {"x": x, "y": y}
    except Exception:
        return None


KEY_DELAY_MS = 12


def typing_timeout(text, floor=4000):
    """Milliseconds Playwright needs to press `text` key by key: the 12 ms per-key delay plus the key events' round
    trips, with the same again for a busy host, capped under the 12 s field write budget (forms.FIELD_WRITE_SECONDS).
    A fixed 4 s allowance cannot type more than about 250 characters even on an idle machine, so a long answer to a
    control that rejects fill() timed out half typed (found as a load-sensitive test failure, 2026-10-20)."""
    return max(floor, min(11_000, 1500 + 30 * len(str(text))))


async def human_type(el, value, timeout=4000, *, retype=False, rematch=None):
    """Fill and read back a plain input; use keys only when the write is rejected."""
    text = str(value)
    try:
        if text and not retype and (await el.input_value(timeout=1000)).strip() == text.strip():
            return el  # Already shows this value (Eightfold pre-fills a remembered, often read-only email).
    except Exception:
        pass
    # Plain inputs accept input/change events from fill; verify before paying
    # for character-by-character events. Masked phone/date controls have their
    # own drivers, and controls that reject fill get one bounded keyboard pass.
    if retype:
        await el.fill("", timeout=timeout)
        if rematch and await el.count() != 1:
            el = await rematch()
    await el.fill(text, timeout=timeout)
    if rematch and await el.count() != 1:
        el = await rematch()
    await el.blur(timeout=min(timeout, 1000))
    if rematch and await el.count() != 1:
        el = await rematch()
    if (await el.input_value(timeout=min(timeout, 1000))) == text:
        return el
    await el.fill("", timeout=timeout)
    if rematch and await el.count() != 1:
        el = await rematch()
    await el.press_sequentially(text, delay=KEY_DELAY_MS, timeout=typing_timeout(text, timeout))
    return el


FILE_BUTTON = re.compile(
    r"(?:browse(?: files?)?|upload|attach|(?:choose|select|upload|attach) (?:a |your |the )?(?:files?|resume|cv|documents?))"
    r"(?:\.\.\.)?",
    re.I,
)


def opens_resume_chooser(label, page_text, uploaded):
    """A plain Browse/Upload button on a page that asks for a CV/resume (SuccessFactors)."""
    label = str(label or "").strip()
    return (
        not uploaded
        and bool(FILE_BUTTON.fullmatch(label))
        and bool(re.search(r"\b(?:cv|resume|r\u00e9sum\u00e9)\b", str(page_text or ""), re.I))
        and not re.search(r"cover|letter|transcript|portfolio", label, re.I)
    )


async def click_for_file_chooser(page, click, path, timeout=5000):
    """Run click(); if it opens a native file chooser, answer it with `path`. True if attached."""
    from playwright.async_api import TimeoutError as PlaywrightTimeoutError

    try:
        async with page.expect_file_chooser(timeout=timeout) as pending:
            await click()
        chooser = await pending.value
    except PlaywrightTimeoutError:
        return False  # The click happened; it simply did not open a chooser.
    await chooser.set_files(str(path), timeout=15000)
    return True


async def page_is_stalled_shell(page):
    """A Workday page whose app never rendered: blank, or only the header/footer shell with
    a spinner. Every real Workday page (posting, sign-in, form, error) carries
    data-automation-id attributes."""
    try:
        return await page.evaluate(
            """() => {
              if(!document.body) return true;
              const blank = !document.body.innerText.trim() && !document.querySelector('input,button,a[href],select,textarea,[role=button]');
              return blank || document.querySelectorAll('[data-automation-id]').length === 0;
            }"""
        )
    except Exception:
        return False


async def automation_ids(page, limit=12):
    """Up to `limit` distinct data-automation-id values on the page, in DOM order."""
    try:
        ids = await page.evaluate(
            """(limit) => [...new Set([...document.querySelectorAll('[data-automation-id]')]
                 .map(el => el.getAttribute('data-automation-id')).filter(Boolean))].slice(0, limit)""",
            limit,
        )
    except Exception:
        return []
    return [str(value)[:80] for value in ids]


async def reset_origin_data(page, url):
    """Drop everything the browser stored for one site (cookies, local storage, IndexedDB,
    caches, service workers) so it loads as in a fresh browser. Other sites are untouched."""
    from urllib.parse import urlsplit

    parts = urlsplit(url)
    if parts.scheme not in {"http", "https"} or not parts.netloc:
        return False
    try:
        cdp = await page.context.new_cdp_session(page)
        try:
            await cdp.send(
                "Storage.clearDataForOrigin",
                {"origin": f"{parts.scheme}://{parts.netloc}", "storageTypes": "all"},
            )
        finally:
            await cdp.detach()
        return True
    except Exception:
        return False


async def page_is_blank(page):
    """A single-page app that never rendered: no text and nothing to interact with."""
    try:
        return await page.evaluate(
            """() => !(document.body && document.body.innerText.trim()) &&
                     !document.querySelector('input,button,a[href],select,textarea,[role=button]')"""
        )
    except Exception:
        return False


async def keystroke_phone(el):
    """A phone input whose widget only trusts key events (Greenhouse fieldset.phone-input)."""
    try:
        return await el.evaluate(
            "e => !!e.closest('fieldset.phone-input,.phone-input,.phone-input__phone')", timeout=1000
        )
    except Exception:
        return False


def national_digits(value):
    """Digits of a US number without its +1 prefix; other numbers keep all their digits."""
    digits = re.sub(r"\D", "", str(value))
    return digits[1:] if len(digits) == 11 and digits.startswith("1") else digits


async def phone_rejected(el, value, *, rematch=None):
    """After a value write: did the phone widget drop or refuse the number?"""
    want = re.sub(r"\D", "", str(value))
    try:
        await el.blur(timeout=1500)
        await asyncio.sleep(0.3)
        if rematch and await el.count() != 1:
            el = await rematch()
        return await el.evaluate(
            """(e, want) => {
              const got = (e.value || '').replace(/\\D/g, '');
              if (!got || !want.endsWith(got.slice(-Math.min(got.length, want.length)))) return true;
              if (got.length < Math.min(7, want.length)) return true;
              const iti = window.intlTelInputGlobals && window.intlTelInputGlobals.getInstance
                && window.intlTelInputGlobals.getInstance(e);
              if (iti && typeof iti.isValidNumber === 'function' && iti.isValidNumber() === false) return true;
              if (e.getAttribute('aria-invalid') === 'true') return true;
              const box = e.closest('.field-wrapper,.input-wrapper,[class*=field],[class*=phone]') || e.parentElement;
              return /too short|invalid phone|not a valid|enter a valid/i.test(box ? box.innerText : '');
            }""",
            want,
            # Short: a re-rendered Workday input otherwise waits the 30 s default (61 s per phone).
            timeout=1500,
        )
    except Exception:
        if rematch:
            raise  # An unreadable replacement is not proof the phone committed.
        return False


async def nearby_text(el, levels=3):
    """Text around a control (its label, hints and format notes), up to a few ancestors and ~600 characters."""
    try:
        return await el.evaluate(
            """(e, n) => { let t = ''; for (let p = e.parentElement, i = 0; p && i < n; p = p.parentElement, i++) {
                 const x = p.innerText || ''; if (x.length > 600) break; t = x; } return t; }""",
            levels,
            timeout=1000,
        )
    except Exception:
        return ""




DETACHED = re.compile(r"not attached to the DOM|detached from (?:the )?document|node is detached", re.I)


async def dispatch_owned_click(handle, *, pointer_sequence=False):
    """Dispatch once only when this exact visible enabled node owns its hit point."""
    return await handle.evaluate("""(e,pointerSequence)=>{
                  if(!e.isConnected || e.disabled || e.getAttribute('aria-disabled')==='true' || e.closest('[inert]'))return false;
                  e.scrollIntoView({block:'center',inline:'nearest'});
                  const r=e.getBoundingClientRect(),s=getComputedStyle(e);
                  if(e.disabled||e.getAttribute('aria-disabled')==='true'||e.closest('[inert]')||!r.width||!r.height||s.visibility==='hidden'||s.display==='none'||s.pointerEvents==='none')return false;
                  const x=r.x+r.width/2,y=r.y+r.height/2;
                  let hit=e.ownerDocument.elementFromPoint(x,y);
                  // A document hit on an open shadow component is its host,
                  // not the actual control/option. Inspect that same click point.
                  for(let depth=0;depth<20 && hit?.shadowRoot;depth++){
                    const inner=hit.shadowRoot.elementFromPoint(x,y);
                    if(!inner || inner===hit)break;
                    hit=inner;
                  }
                  let owned=false;
                  for(let n=hit;n;n=n.assignedSlot||n.parentElement||n.getRootNode()?.host){
                    if(n===e){owned=true;break;}
                  }
                  if(!owned)return false;
                  if(pointerSequence){
                    // A real field click starts with pointerdown/mousedown; React
                    // selects and other controls may open there instead of onclick.
                    // Stop on replacement so a detached node cannot receive a late click.
                    const options={bubbles:true,cancelable:true,composed:true,
                      clientX:x,clientY:y,button:0,buttons:1,pointerId:1,pointerType:'mouse',isPrimary:true};
                    const pointerAllowed=e.dispatchEvent(new PointerEvent('pointerdown',options));
                    if(!e.isConnected)return true;
                    let mouseAllowed=false;
                    if(pointerAllowed)mouseAllowed=e.dispatchEvent(new MouseEvent('mousedown',options));
                    if(!e.isConnected)return true;
                    if(pointerAllowed && mouseAllowed)e.focus({preventScroll:true});
                    if(!e.isConnected)return true;
                    e.dispatchEvent(new PointerEvent('pointerup',{...options,buttons:0}));
                    if(!e.isConnected)return true;
                    if(pointerAllowed)e.dispatchEvent(new MouseEvent('mouseup',{...options,buttons:0}));
                    if(!e.isConnected || e.disabled || e.getAttribute('aria-disabled')==='true')return true;
                  }
                  e.click();return true;
                }""", pointer_sequence)


async def safe_click(el, timeout=4000, *, field_control=False, _reresolved=0):
    # Keep an exact node handle through React replacement and type restoration.
    await ensure_uncovered(el)
    owned_handle = hasattr(el, "element_handle")
    handle = await el.element_handle(timeout=min(timeout, 1000)) if owned_handle else el
    if handle is None:
        raise ValueError("The field control is no longer available")
    # Idempotent: a late first run must not make a retry read back type=button.
    prepare = """(e,protect) => {
      if(!('jpOriginalType' in e.dataset))e.dataset.jpOriginalType=e.getAttribute('type')??'\\u0000';
      const t=e.dataset.jpOriginalType==='\\u0000'?null:e.dataset.jpOriginalType;
      if(protect&&e.tagName==='BUTTON')e.type='button';return t}"""
    try:
        async with asyncio.timeout(max(1, timeout / 1000)):
            original = await handle.evaluate(prepare, field_control)
    except TimeoutError:
        # A trivial attribute read only stalls while the renderer is busy
        # (Workday re-rendering after a delete, a throttled background tab).
        # Give the same node one bounded longer window instead of failing the run.
        async with asyncio.timeout(10):
            original = await handle.evaluate(prepare, field_control)
    detached = False
    click_started = False
    try:
        try:
            if field_control:
                # Background Chrome throttles animation frames. Its stability wait
                # costs seconds per field even when the exact option is ready.
                # The existing visibility/enabled/coverage checks permit this
                # field action immediately. Final actions keep native clicks.
                if await dispatch_owned_click(handle, pointer_sequence=True):
                    return
            position = await pointer_path(handle)
            await handle.evaluate("""e => {
                if(e.__jpClickObserver)e.removeEventListener('click',e.__jpClickObserver,true);
                e.__jpClickObserved=false;
                e.__jpClickObserver=()=>{e.__jpClickObserved=true};
                e.addEventListener('click',e.__jpClickObserver,{once:true,capture:true});
            }""")
            click_started = True
            await handle.click(timeout=timeout, **({"position": position} if position else {}))
        except Exception as exc:
            if owned_handle and _reresolved < 3 and DETACHED.search(str(exc)):
                detached = True
            elif "Timeout" not in type(exc).__name__:
                raise
            else:
                # A timeout can occur AFTER dispatch, while navigation is still
                # loading. Never replay an observed click, especially Submit.
                if await handle.evaluate("e=>e.__jpClickObserved===true"):
                    return
                # Background desktop tabs can stop requestAnimationFrame, so the
                # stability check times out on a stationary, usable field choice.
                # Dispatch on the exact node only after verifying visibility,
                # enabled state and that no other element covers its click point.
                clicked = await dispatch_owned_click(handle)
                if not clicked:
                    raise
    except Exception as exc:
        # The click can navigate while Playwright is waiting for it to finish,
        # destroying this handle's context before the timeout fallback reads it.
        # Stop using the old node and let the caller observe the destination;
        # this is not submission evidence and must never replay the click.
        if not click_started or field_control or "Execution context was destroyed" not in str(exc):
            raise
    finally:
        try:
            async with asyncio.timeout(0.5):
                await handle.evaluate(
                    "(e,args)=>{if(e.__jpClickObserver)e.removeEventListener('click',e.__jpClickObserver,true);delete e.__jpClickObserver;delete e.__jpClickObserved;delete e.dataset.jpOriginalType;if(args.protect&&e.tagName==='BUTTON'){if(args.original===null)e.removeAttribute('type');else e.setAttribute('type',args.original)}}",
                    {"original": original, "protect": field_control},
                )
        except Exception:
            pass  # The React node was replaced after committing the choice.
        finally:
            if owned_handle:
                await handle.dispose()
    if detached:
        # Dayforce (NCMEC 2026-10-16): React replaced the node between resolving and clicking. Taleo (Bealls
        # 2026-10-17) re-rendered its Apply button twice while loading. Playwright refuses a detached node before
        # dispatching anything, so the locator's current node gets the click after a short settle, up to 3 times.
        await asyncio.sleep(0.3 * (_reresolved + 1))
        await safe_click(el, timeout, field_control=field_control, _reresolved=_reresolved + 1)


async def field_click(el, timeout=4000):
    return await safe_click(el, timeout, field_control=True)


def _preferred(opts, prefer):
    """The owner-policy equivalent among the options in view (no model call), as an option record."""
    real = [o for o in opts if not o["disabled"]]
    picked = prefer([o["text"] for o in real]) if prefer and real else None
    return next((o for o in real if o["text"] == picked), None)


async def dropdown(frame, el, value, choose=None, prefer=None):
    """Button with aria-haspopup=listbox (Workday, MUI). Returns the committed option text.

    `prefer` is a cheap, model-free choice among the options in view (the owner's degree/major equivalents); it runs
    on every window. `choose` may consult the answer model and runs once, last."""
    await dismiss_pickers(frame)
    await field_click(el, timeout=4000)
    scope = await _popup_scope(el)
    opts = await wait_options(frame, scope, timeout=3, owner=el)
    scope = await _popup_scope(el) or scope
    choice = best_option([o for o in opts if not o["disabled"]], value) or _preferred(opts, prefer)
    # React portals can replace their listbox ID between opening and rendering.
    # Read the currently visible choices before trying type-ahead against a stale scope.
    if not choice and scope:
        scope = await _popup_scope(el) or scope
        current = await visible_options(frame, scope)
        if current:
            opts = current
            choice = best_option([o for o in opts if not o["disabled"]], value)
    if not choice:
        choice = await scroll_for_option(frame, value, scope)
    # Long virtualized lists render a window of options; type-ahead moves it ("NY" -> "new york"). A short list
    # that does not scroll is complete: type-ahead cannot reveal another row, so it goes straight to the
    # applicant-context choice (Workday Degree GED/AA/BA/BS/...: "BE" spent 8.3 s typing before policy chose BS).
    complete = bool(opts) and not choice and not await options_scroll(frame, opts)
    aliases = sorted(_alias_group(value) - {norm(value)}, key=len, reverse=True)[:3]
    # Full names first: type-ahead on "bachelor of engineering" reaches the Bachelor rows, where the owner's
    # equivalent ("Bachelor's Degree") is in view. Typing "BE" first, then the aliases, each after a 0.9 s pause,
    # outlasted the 12 s field deadline on 39 Workday jobs' Education 2 Degree (ABB, Broadridge, 2026-10-18/19).
    for attempt, typed in enumerate([] if complete else [*aliases, str(value)]):
        if choice:
            break
        if attempt:
            await asyncio.sleep(0.9)  # let the widget's type-ahead buffer reset
        await frame.page.keyboard.type(typed[:24], delay=12)
        await asyncio.sleep(0.35)
        opts = await wait_options(frame, scope, timeout=1.5, owner=el)
        choice = best_option([o for o in opts if not o["disabled"]], value) or _preferred(opts, prefer)
    if not choice and choose and opts:
        real = [o for o in opts if not o["disabled"]]
        selected = await choose([o["text"] for o in real]) if real else None
        choice = next((o for o in real if o["text"] == selected), None)
    if not choice:
        await frame.page.keyboard.press("Escape")
        raise ValueError("The desired option is not offered")
    await click_option(frame, choice, owner=el)
    await close_picker(el, frame)
    for _ in range(20):
        text = norm(await el.inner_text(timeout=2000))
        if text and (text == norm(choice["text"]) or norm(choice["text"]) in text):
            return choice["text"]
        await asyncio.sleep(0.1)
    raise ValueError("The website did not commit this menu's selected option")


async def open_combobox(el):
    try:
        await field_click(el, timeout=2000)
    except Exception:
        # Slotted multiselect search inputs can be covered by their host trigger.
        handle = await el.evaluate_handle("e=>e.closest('.c-spl-multiselect-autocomplete-trigger')")
        trigger = handle.as_element()
        if not trigger or not await trigger.is_visible():
            raise
        await trigger.click(timeout=2000)
    # A click on an already-open (or focus-toggled) menu can leave it closed:
    # the field then reads as offering no options. Open an editable input's
    # own listbox with ArrowDown, which never commits a choice on its own.
    try:
        closed = await el.evaluate(
            "e => e.getAttribute('aria-expanded') === 'false' && ['INPUT','TEXTAREA'].includes(e.tagName)"
        )
        if closed:
            await asyncio.sleep(0.15)
            if await el.get_attribute("aria-expanded", timeout=500) == "false":
                await el.press("ArrowDown", timeout=1000)
    except Exception:
        pass


async def close_picker(el, frame=None):
    """Close the observed owner's menu; Workday can ignore a page-wide Escape."""
    try:
        # Closing is an operation on an open menu, not a reset of its field.
        # Oracle CX treats Escape on a closed, selected input as Clear. Avoid
        # both the key and document-wide dismissal when this owner is closed;
        # a sibling's popup is not ours to dismiss here.
        if await el.get_attribute("aria-expanded", timeout=1000) == "false":
            return
        await el.press("Escape", timeout=1000)
        if await el.get_attribute("aria-expanded", timeout=1000) == "true":
            if await el.evaluate("e=>e.matches('button,[role=button]')"):
                await field_click(el, timeout=1500)
            else:
                await el.blur()
    except Exception:
        pass  # Read-back still decides whether a selection was accepted.
    # Escape and aria-expanded are incomplete on several Workday tenants.
    # Dispatch an outside click on the document itself: this invokes dismissal
    # listeners without hit-testing (and accidentally selecting) an overlay option.
    try:
        await el.evaluate(OUTSIDE_PICKER_CLICK)
        if frame is not None:
            await dismiss_pickers(frame)
    except Exception:
        pass


OUTSIDE_PICKER_CLICK = """() => {
  for (const type of ['pointerdown', 'mousedown', 'pointerup', 'mouseup', 'click']) {
    const EventType = type.startsWith('pointer') ? PointerEvent : MouseEvent;
    document.body.dispatchEvent(new EventType(type, {bubbles:true, button:0, pointerId:1}));
  }
}"""


OPEN_LISTS = (
    "[role=listbox]:not([data-automation-id=\"selectedItemList\"]):not([aria-label=\"items selected\"]),[role=menu]"
)

# One call: the outside click, the toggle of pickers that ignore it, and a count of the lists still visible. Every
# picker write starts here, and the three calls it used to make (plus a wait_for_function that answered at once on a
# quiet page) were five protocol calls before the first option was read.
DISMISS_PICKERS_JS = (
    "() => {\n  ("
    + OUTSIDE_PICKER_CLICK
    + """)();
  // Workday's questionnaire pickers close through their own trigger and can
  // ignore both Escape and outside clicks. Toggle only explicitly open,
  // observed field pickers; never a navigation or submit control.
  for (const e of document.querySelectorAll('[aria-haspopup=listbox][aria-expanded=true]')) {
    if (!e.getClientRects().length || !e.matches('button,[role=button]')) continue;
    const original=e.getAttribute('type');
    if(e.tagName==='BUTTON')e.type='button';
    e.click();
    if(e.tagName==='BUTTON'){if(original===null)e.removeAttribute('type');else e.setAttribute('type',original)}
  }
  return [...document.querySelectorAll('"""
    + OPEN_LISTS
    + """')]
    .filter(e=>e.getClientRects().length && getComputedStyle(e).visibility!=='hidden').length;
}"""
)


async def dismiss_pickers(frame):
    """Ensure a picker can only read its own choices, including ARIA-poor portals."""
    if not await frame.evaluate(DISMISS_PICKERS_JS):
        return
    try:
        await frame.wait_for_function(
            """() => ![...document.querySelectorAll('[role=listbox]:not([data-automation-id="selectedItemList"]):not([aria-label="items selected"]),[role=menu]')]
          .some(e=>e.getClientRects().length && getComputedStyle(e).visibility!=='hidden')""",
            timeout=600,
            polling=50,
        )
        return
    except Exception:
        pass
    remaining = await frame.evaluate("""() => [...document.querySelectorAll('[role=listbox]:not([data-automation-id="selectedItemList"]):not([aria-label="items selected"]),[role=menu]')]
      .filter(e=>e.getClientRects().length && getComputedStyle(e).visibility!=='hidden').length""")
    if remaining:
        # Workday uses trusted pointer events for some outside-click listeners.
        # Observe a blank, noninteractive point; never force a click through a menu.
        point = await frame.evaluate("""() => {
          const blocked='button,a,input,textarea,select,label,[role=button],[role=option],[role=listbox],[role=menu],[aria-haspopup],[onclick]';
          for(const y of [6,30,60,100,150,200,window.innerHeight-8]) {
            for(const x of [6,30,window.innerWidth-8,window.innerWidth*.5]) {
              const e=document.elementFromPoint(x,y);
              if(e && !e.closest(blocked)) return {x,y};
            }
          }
          return null;
        }""")
        if point:
            offset = {"x": 0, "y": 0}
            if frame.parent_frame:
                handle = await frame.frame_element()
                box = await handle.bounding_box()
                await handle.dispose()
                if box:
                    offset = box
                else:
                    point = None
            if point:
                await frame.page.mouse.click(point["x"] + offset["x"], point["y"] + offset["y"])
        await frame.page.keyboard.press("Escape")
        await frame.evaluate(OUTSIDE_PICKER_CLICK)
        try:
            await frame.wait_for_function(
                """() => ![...document.querySelectorAll('[role=listbox]:not([data-automation-id="selectedItemList"]):not([aria-label="items selected"]),[role=menu]')]
              .some(e=>e.getClientRects().length && getComputedStyle(e).visibility!=='hidden')""",
                timeout=600,
                polling=50,
            )
            return
        except Exception:
            pass
        remaining = await frame.evaluate("""() => [...document.querySelectorAll('[role=listbox]:not([data-automation-id="selectedItemList"]):not([aria-label="items selected"]),[role=menu]')]
          .filter(e=>e.getClientRects().length && getComputedStyle(e).visibility!=='hidden').length""")
    if remaining:
        raise ValueError("Previous dropdown remains open; its choices cannot be reused")


def school_other_option(offered):
    return bool(
        re.fullmatch(
            r"other(?: school| institution| university| college)?(?: please specify| not listed)?|"
            r"(?:school|institution|university|college) not (?:listed|mentioned|found)|not listed|"
            r"other\s*\([^)]*(?:school|institution|university|college|list|specify)[^)]*\)",
            str(offered).strip(),
            re.I,
        )
    )


def employer_other_option(offered):
    """An employer list's own "not listed" choice; "None" (no employer at all) never qualifies."""
    return bool(
        re.fullmatch(
            r"other(?: employer| company| organi[sz]ation| firm)?(?: please specify| not listed)?|"
            r"(?:employer|company|organi[sz]ation|firm) not (?:listed|found|mentioned)|not listed|none of the above",
            str(offered).strip(),
            re.I,
        )
    )


# Catalogs abbreviate the institution: Jefferson Health's Workday offers "Univ of <State> <Campus>" (2026-10-23).
SCHOOL_ABBREVIATIONS = {"univ": "university", "inst": "institute", "coll": "college"}


def school_norm(text):
    return " ".join(SCHOOL_ABBREVIATIONS.get(word, word) for word in norm(text).split())


def school_option_matches(value, offered):
    """A shorter search query cannot silently select a different campus."""
    if school_other_option(offered):
        return True
    institution, separator, campus = str(value).partition(",")
    if not separator:
        return school_norm(value) == school_norm(offered)
    institution, campus, display = school_norm(institution), school_norm(campus), school_norm(offered)
    if not display.startswith(institution):
        return False
    suffix = display[len(institution) :].strip()
    return not suffix or bool(campus and campus in suffix)


UI5_OPTION_SELECTOR = (
    "[role=option][class*=sapM],li.sapMSLI,.sapMSelectList [role=option],.sapMSelectList li,"
    "[role=listbox] li.sapMSLI"
)
UI5_OPTION_TIMEOUT = 3.0
NO_OPTIONS_READABLE = "No options became readable for this picker"


RCM_MARK = """() => {
  document.querySelectorAll('[data-ap-seen],[data-ap-pick]').forEach(n => {
    n.removeAttribute('data-ap-seen');n.removeAttribute('data-ap-pick');
  });
  for (const n of document.querySelectorAll('body *'))
  if (n.offsetParent !== null || n.getClientRects().length) n.setAttribute('data-ap-seen', '1'); }"""

# Visible elements that appeared after the mark and read exactly as one of the targets;
# the deepest match is the clickable list entry.
RCM_FIND = """targets => {
  const norm = t => (t || '').replace(/\\s+/g, ' ').trim().toLowerCase();
  const want = new Set(targets.map(norm));
  const hits = [...document.querySelectorAll('body *')].filter(n =>
    !n.hasAttribute('data-ap-seen') && n.getClientRects().length && !['INPUT', 'SCRIPT', 'STYLE'].includes(n.tagName)
    && want.has(norm(n.textContent)));
  const leaf = hits.filter(n => !hits.some(m => m !== n && n.contains(m)));
  document.querySelectorAll('[data-ap-pick]').forEach(n => n.removeAttribute('data-ap-pick'));
  if (leaf.length === 1) leaf[0].setAttribute('data-ap-pick', '1');
  return leaf.length;
}"""


# Leaf entries that appeared after the mark (the open list), for matching a differently spelled entry.
RCM_LIST = """() => {
  const seen = new Set();
  return [...document.querySelectorAll('body *')].filter(n => !n.hasAttribute('data-ap-seen')
      && n.getClientRects().length && !['INPUT', 'SCRIPT', 'STYLE'].includes(n.tagName) && !n.children.length)
    .map(n => (n.textContent || '').replace(/\\s+/g, ' ').trim())
    .filter(t => t && t.length < 160 && !seen.has(t) && seen.add(t)).slice(0, 300);
}"""


# One read of an RCM input's own list: "{n}:_input" owns "{n+1}:_listSelect", but the scroll box div#"{n+1}:" exists
# only after the first DWR answer (0.5-0.9 s after the click), and every fetched page renders its OWN <ul> with that
# same id, so getElementById saw page 1 only (REA on career8.successfactors.com, 2026-10-25).
RCM_CATALOG = """e => {
  const own = e.getAttribute('aria-owns') || '';
  const box = document.getElementById(own.replace(/_listSelect$/, ''));
  const lis = [...document.querySelectorAll('ul[id="' + own + '"] li[role=option]')];
  return {box: !!box, loading: !!(box && box.querySelector('.page-loading')),
          none: !!(box && box.querySelector('.no-results')),
          options: lis.filter(o => o.id && o.getClientRects().length)
            .map(o => ({id: o.id, text: (o.textContent || '').replace(/\\s+/g, ' ').trim()}))};
}"""
RCM_SCROLL_END = """e => { const b = document.getElementById((e.getAttribute('aria-owns') || '').replace(/_listSelect$/, ''));
  if (b) b.scrollTop = b.scrollHeight; return !!b; }"""
RCM_PAGE = 100


async def _rcm_catalog(el, settle=0.9, limit=3.0, until=None):
    """Poll the owned list until it has answered (its box exists, no page is loading) and stopped changing."""
    end = time.monotonic() + limit
    last, stable = None, 0.0
    state = {"box": False, "options": [], "none": False, "loading": False}
    while time.monotonic() < end:
        state = await el.evaluate(RCM_CATALOG)
        key = [o["id"] for o in state["options"]]
        if state["box"] and not state["loading"] and (key or state["none"]):
            stable = stable + 0.1 if key == last else 0.0
            # A page shorter than a full page is the whole catalog: nothing more can arrive.
            if stable >= settle or len(key) % RCM_PAGE or (until and until(state)):
                break
        else:
            stable = 0.0
        last = key
        await asyncio.sleep(0.1)
    return state


async def _rcm_all_pages(el, state):
    """Scroll the list to its end until no further page arrives (a page at a time, fetched in order)."""
    for _ in range(8):
        if len(state["options"]) % RCM_PAGE or not state["options"]:
            break
        count = len(state["options"])
        await el.evaluate(RCM_SCROLL_END)
        await asyncio.sleep(0.25)
        state = await _rcm_catalog(el, settle=0.3, limit=1.5, until=lambda s: len(s["options"]) > count)
        if len(state["options"]) == count:
            break
    return state


def _rcm_match(text, targets, options):
    wanted = {norm(t) for t in targets}
    exact = next((o for o in options if norm(o["text"]) in wanted), None)
    if exact:
        return exact
    texts = [o["text"] for o in options if o["text"]]
    pick = next((t for t in texts if school_option_matches(text, t)), None) or best_option(texts, text)
    return next((o for o in options if o["text"] == pick), None)


async def _rcm_owned_select(frame, el, text, targets, base, longest, full_list_only=False, choose=None):
    """Open the whole list by its button (no typing), read every page, and decide on the real options; search the
    employer's "contains" filter once only when the catalog is longer than the pages that loaded. Typed searches ran
    first and waited 4 s each for an exact entry, so every answer spelled unlike its option burned the 12 s field
    budget before the full list and the resolver were tried (139 deadlines in 36 runs, REA 2026-10-25)."""
    await el.fill("", timeout=3000)
    await frame.locator(f"[id='{base}_selectButton']").click(timeout=3000)
    state = await _rcm_all_pages(el, await _rcm_catalog(el))
    options = [o for o in state["options"] if o["text"] and norm(o["text"]) != "no selection"]
    complete = len(state["options"]) % RCM_PAGE != 0
    pick = _rcm_match(text, targets, options)
    if not pick and choose and options and (complete or full_list_only):
        chosen = await choose([o["text"] for o in options])
        pick = next((o for o in options if o["text"] == chosen), None) if chosen else None
    if not pick and not complete and not full_list_only:
        word = max(re.findall(r"[A-Za-z]{4,}", longest) or [longest[:4]], key=len)
        before = [o["id"] for o in state["options"]]
        await el.fill("")
        await el.press_sequentially(word, delay=30)
        state = await _rcm_catalog(
            el,
            settle=0.4,
            limit=3.5,
            until=lambda s: s["none"] or ([o["id"] for o in s["options"]] != before and not s["loading"]),
        )
        options = [o for o in state["options"] if o["text"] and norm(o["text"]) != "no selection"]
        pick = _rcm_match(text, targets, options)
        if not pick and choose and options:
            chosen = await choose([o["text"] for o in options])
            pick = next((o for o in options if o["text"] == chosen), None) if chosen else None
    if not pick:
        try:
            await el.press("Escape", timeout=1000)
        except Exception:
            pass
        raise ValueError("No matching option is offered")
    targets = [*targets, pick["text"]]
    await frame.locator(f"[id='{pick['id']}']").click(timeout=3000)
    if await _rcm_committed(el, targets, owned=True):
        return await el.evaluate("e => e.value")
    if await _rcm_enter(el, pick["id"]) and await _rcm_committed(el, targets, owned=True):
        return await el.evaluate("e => e.value")
    raise ValueError("The website did not commit the observed choice")


async def _rcm_paginated(frame, el, value, full_list_only=False, choose=None):
    """SuccessFactors RCM paginated picklist (ASSA ABLOY State/Ethnicity, 2026-10-14).

    An input plus a ``<id>_selectButton``; its list renders outside the field without
    option roles, so no option scan sees it, and a typed value is discarded unless a
    list entry is clicked. Type a prefix (or open the full list), click the entry whose
    text is exactly the answer (or its state-name alias), then read the input back.
    """
    from .answers import US_STATES

    if await el.is_disabled():
        # A dependent picklist waits for an earlier answer; filling it waited 3 s and then timed out.
        raise ValueError("This list depends on an earlier answer")
    text = str(value).strip()
    targets = [text, US_STATES.get(text.upper(), "")] + [
        k for k, v in US_STATES.items() if v.lower() == text.lower()
    ]
    targets = [t for t in dict.fromkeys(targets) if t]
    base = (await el.get_attribute("id") or "").removesuffix("_input")
    longest = max(targets, key=len)
    if await el.get_attribute("aria-owns"):
        return await _rcm_owned_select(frame, el, text, targets, base, longest, full_list_only, choose)
    # A 4-letter prefix of a long catalog entry ("Univ" for a university) leaves hundreds of paged
    # entries (Cargill, 2026-10-16); type the whole answer, then its most distinctive word.
    queries = (
        [longest[:4]]
        if len(longest) <= 12
        else [longest[:40], max(re.findall(r"[A-Za-z]{4,}", longest) or [longest[:4]], key=len)]
    )
    for attempt in ["button"] if full_list_only else [*queries, "button"]:
        await frame.evaluate(RCM_MARK)
        if attempt != "button":
            await el.click(timeout=3000)
            await el.fill("", timeout=3000)
            await el.press_sequentially(attempt, delay=40)
        else:
            await el.fill("", timeout=3000)
            await frame.locator(f"[id='{base}_selectButton']").click(timeout=3000)
        owned = await _rcm_owned_choice(el, text, targets, typed=attempt != "button", choose=choose)
        if owned is not None:
            # The listbox the input owns (aria-owns, Capgemini 2026-10-18): its real options, once the
            # employer's search has answered. Click the one chosen; Enter on it if the click did not commit.
            option = owned
            if option:
                targets = [*targets, option["text"]]
                await frame.locator(f"[id='{option['id']}']").click(timeout=3000)
                if await _rcm_committed(el, targets, owned=True):
                    return await el.evaluate("e => e.value")
                if await _rcm_enter(el, option["id"]) and await _rcm_committed(el, targets, owned=True):
                    return await el.evaluate("e => e.value")
        else:
            found = 0
            for _ in range(16):
                await asyncio.sleep(0.25)
                found = await frame.evaluate(RCM_FIND, targets)
                if found:
                    break
            if not found:
                # The entry can be spelled differently from the answer ("University of Michigan-Ann Arbor").
                listed = await frame.evaluate(RCM_LIST)
                pick = next((t for t in listed if school_option_matches(text, t)), None) or best_option(
                    listed, text
                )
                if not pick and choose and listed and attempt == "button":
                    pick = await choose(listed)
                if pick:
                    found = await frame.evaluate(RCM_FIND, [pick])
                    targets = [*targets, pick]
            if found == 1:
                await frame.locator("[data-ap-pick]").first.click(timeout=3000)
                if await _rcm_committed(el, targets):
                    return await el.evaluate("e => e.value")
        try:
            await el.press("Escape", timeout=1000)
        except Exception:
            pass
    raise ValueError("The website did not commit the observed choice")


# Visible options of the listbox an RCM input owns ("{n}:_input" owns "{n+1}:_listSelect"), or null without one.
RCM_OWNED = """e => {
  const id = e.getAttribute('aria-owns') || e.getAttribute('aria-controls');
  const list = id && document.getElementById(id);
  if (!list) return null;
  return [...list.querySelectorAll('[role=option]')].filter(o => o.id && o.getClientRects().length)
    .map(o => ({id: o.id, text: (o.textContent || '').replace(/\\s+/g, ' ').trim()}));
}"""


async def _rcm_owned_choice(el, text, targets, typed, choose=None):
    """The option to pick from the input's own listbox: None when it owns none, {} when nothing matches.

    Options load 100 at a time and the employer searches about 1.4 s after typing starts, so the
    first page can show while the answer ("United States", position 230 of 253) is still on its way:
    wait for an exact entry, or for the list to settle, before matching a different spelling."""
    wanted = {norm(t) for t in targets}
    options, last, steady = None, None, 0
    for tick in range(32):
        await asyncio.sleep(0.25)
        options = await el.evaluate(RCM_OWNED)
        if options is None:
            return None
        if exact := next((o for o in options if norm(o["text"]) in wanted), None):
            return exact
        texts = [o["text"] for o in options]
        steady = steady + 1 if texts == last else 0
        last = texts
        if steady >= 4 and (tick >= 15 or not typed):
            break
    texts = [o["text"] for o in options or []]
    pick = next((t for t in texts if school_option_matches(text, t)), None) or best_option(texts, text)
    if not pick and choose and texts and not typed:
        # The full list's own entries, chosen by the resolver when the answer's wording is not among them (NetApp
        # 2026-10-22: Veteran Status offered its own phrasings; ten agent attempts never committed one).
        pick = await choose(texts)
    return next((o for o in options or [] if o["text"] == pick), {})


# Whether the owned list is still open, and whether the field's hidden value input (the option id) is filled.
RCM_STATE = """e => {
  const id = e.getAttribute('aria-owns') || e.getAttribute('aria-controls');
  const list = id && document.getElementById(id);
  const box = e.closest('.sfCascadingPicklist, [id$="_selectContainer"]');
  const values = box ? [...box.querySelectorAll('input[type=hidden]')] : [];
  return {open: !!(list && !list.hidden && list.getClientRects().length),
          hidden: values.length ? values.some(h => h.value) : null};
}"""


async def _rcm_committed(el, targets, owned=False):
    """The input shows the picked entry and keeps it when it loses focus: RCM's _onBlur reverts typed text that
    was never selected from the list. Typed text equal to the label proves nothing while the list stays open, so
    a selection must also close the owned list and fill the field's hidden value."""
    await asyncio.sleep(0.4)
    if owned:
        state = await el.evaluate(RCM_STATE)
        if state["open"] or state["hidden"] is False:
            return False
    try:
        await el.evaluate("e => e.blur()")
    except Exception:
        return False
    await asyncio.sleep(0.5)
    actual = norm(await el.evaluate("e => e.value || ''"))
    return bool(actual) and any(actual == norm(t) for t in targets)


async def _rcm_enter(el, option_id):
    """Select the option with the keyboard: arrow to it (aria-activedescendant), then Enter."""
    try:
        await el.focus()
        for _ in range(40):
            if await el.get_attribute("aria-activedescendant") == option_id:
                await el.press("Enter", timeout=1000)
                return True
            await el.press("ArrowDown", timeout=1000)
    except Exception:
        pass
    return False


async def _is_ui5_picker(frame, el):
    """SAP UI5 controls: a sapM control wrapper, or sapM list items already visible."""
    if await el.evaluate("e => !!e.closest('[class*=\"sapM\"]')"):
        return True
    return bool(await visible_options(frame, None, UI5_OPTION_SELECTOR))


async def _ui5_combobox(frame, el, value):
    """UI5 pickers commit only when a list item is clicked and clear aria-invalid only on blur."""
    started = time.monotonic()
    nudged = False
    opts = []
    while True:
        opts = await visible_options(frame, await _popup_scope(el), UI5_OPTION_SELECTOR)
        if opts or time.monotonic() - started >= UI5_OPTION_TIMEOUT:
            break
        if not nudged and time.monotonic() - started >= 1.0:
            # ArrowDown opens an editable UI5 list without committing anything.
            nudged = True
            try:
                await el.press("ArrowDown", timeout=1000)
            except Exception:
                pass
        await asyncio.sleep(0.15)
    if not opts:
        try:
            await el.press("Escape", timeout=1000)
        except Exception:
            pass
        raise ValueError(NO_OPTIONS_READABLE)
    choice = best_option([o for o in opts if not o["disabled"]], value)
    if not choice:
        try:
            await el.press("Escape", timeout=1000)
        except Exception:
            pass
        raise ValueError("No matching option is offered")
    await click_option(frame, choice, owner=el)
    # The control keeps aria-invalid until it loses focus; leaving it commits the readback.
    await el.evaluate("e => e.blur()")
    deadline = time.monotonic() + 2
    while True:
        actual = await el.evaluate("e => e.value || e.textContent || ''")
        # The control may show the full state name for the clicked abbreviation match.
        if norm(actual) and norm(actual) in _alias_group(choice["text"]):
            return choice["text"]
        if time.monotonic() >= deadline:
            raise ValueError("The website did not commit the observed choice")
        await asyncio.sleep(0.1)


async def is_ui5_input(el):
    """A SAP UI5 text control: sapMInputBaseInner inside a sapMInput or sapMComboBox wrapper."""
    try:
        # A short timeout: a locator the site re-rendered would otherwise wait Playwright's 30 s
        # default on every text field (Workday, 2026-10-13: ~30 s per field).
        return await el.evaluate(
            "e => e.classList.contains('sapMInputBaseInner') || !!e.closest('[class*=\"sapMInput\"],[class*=\"sapMComboBox\"]')",
            timeout=1000,
        )
    except Exception:
        return False  # A control the site replaced keeps the generic path's behavior.


async def is_ui5wc_input(el):
    """A UI5 Web Components input (``<ui5-input>``; the inner input lives in its shadow root)."""
    try:
        return await el.evaluate(
            "e => e.classList.contains('ui5-input-inner') || /^UI5-/.test(e.getRootNode().host?.tagName || '')",
            timeout=1000,
        )
    except Exception:
        return False


MONTH_NAMES = (
    "January",
    "February",
    "March",
    "April",
    "May",
    "June",
    "July",
    "August",
    "September",
    "October",
    "November",
    "December",
)
UI5_DATE_TOKENS = re.compile(r"y+|M+|d+|'[^']*'|[^yMd']+")


def format_ui5_date(pattern, month, day, year):
    """Render a date in a UI5/CLDR pattern ("MMM d, yyyy", "MM/dd/yyyy", "dd.MM.y")."""
    out = []
    for token in UI5_DATE_TOKENS.findall(pattern):
        if token[0] == "y":
            out.append(year[-2:] if len(token) == 2 else year)
        elif token[0] == "M":
            m = int(month)
            out.append(
                MONTH_NAMES[m - 1]
                if len(token) >= 4
                else MONTH_NAMES[m - 1][:3]
                if len(token) == 3
                else f"{m:02d}"
                if len(token) == 2
                else str(m)
            )
        elif token[0] == "d":
            out.append(f"{int(day):02d}" if len(token) == 2 else str(int(day)))
        else:
            out.append(token.strip("'"))
    return "".join(out)


# The picker host above an inner input: a real ui5-date-picker wraps an internal ui5-input, so the
# <input> sits two shadow roots below the picker (ASSA ABLOY, 2026-10-15).
# Tags may be scoped (SuccessFactors registers suffixed copies such as ui5-date-picker-<suffix>), so the
# host is recognized by name anywhere in the tag or by the picker's own dateValue/formatPattern API.
UI5_PICKER_HOST = (
    "e => { let n = e; for (let i = 0; i < 5 && n; i++) { const h = n.getRootNode().host;"
    " if (!h) return null; if (/UI5-DATE(?:TIME|RANGE)?-PICKER/.test(h.tagName)"
    " || ('dateValue' in h && 'formatPattern' in h)) return h; n = h; } return null; }"
)


async def is_ui5wc_date(el):
    """The inner input of a UI5 Web Components ``<ui5-date-picker>`` (or one of its range/time kin)."""
    try:
        return await el.evaluate(
            f"e => !!({UI5_PICKER_HOST})(e)"
            " || (e.classList.contains('ui5-input-inner') && e.getAttribute('aria-haspopup') === 'grid')",
            timeout=1000,
        )
    except Exception:
        return False


async def ui5wc_date(el, value):
    """SuccessFactors' application wizard (ASSA ABLOY, 2026-10-15) uses ``ui5-date-picker``: it accepts
    typed text only in its own pattern, shown as the placeholder ("MMM d, yyyy"), and only on Enter.
    Month-only history dates use the first of the month. The picker's parsed date is checked."""
    month, day, year = parse_date(value)
    if not (month and year):
        raise ValueError("The applicant date does not have the precision this field requires")
    day = day or "01"
    info = await el.evaluate(
        f"e => {{ const h = ({UI5_PICKER_HOST})(e) || e.getRootNode().host || e;"
        " return {pattern: h.getAttribute('format-pattern') || h.formatPattern || '',"
        " placeholder: e.placeholder || h.getAttribute('placeholder') || ''}; }"
    )
    patterns = [info["pattern"]]
    if re.search(r"[yMd]", info["placeholder"]) and not re.search(r"\d", info["placeholder"]):
        patterns.append(info["placeholder"].replace("e.g. ", ""))
    patterns += ["MMM d, yyyy", "MM/dd/yyyy", "yyyy-MM-dd"]
    for pattern in dict.fromkeys(p for p in patterns if p):
        text = format_ui5_date(pattern, month, day, year)
        await el.click(timeout=3000)
        await el.fill("", timeout=3000)
        await el.press_sequentially(text, delay=25)
        await el.press("Enter")
        await asyncio.sleep(0.3)
        state = await el.evaluate(
            f"e => {{ const h = ({UI5_PICKER_HOST})(e) || e.getRootNode().host || e; let d = null;"
            " try { d = h.dateValue; } catch (_) {}"
            " return {bad: /^(Negative|Error)$/.test(h.getAttribute('value-state') || h.valueState || ''),"
            " parsed: d && !isNaN(d) ? [d.getFullYear(), d.getMonth() + 1, d.getDate()] : null,"
            " shown: h.value || e.value || ''}; }"
        )
        if (
            not state["bad"]
            and (state["parsed"] in (None, [int(year), int(month), int(day)]))
            and state["shown"]
        ):
            return state["shown"]
    raise ValueError("The date picker did not accept the date in its format")


async def _ui5wc_exact(frame, targets):
    """A visible list entry, anywhere including shadow roots, whose text is exactly a target."""
    for t in targets:
        name = re.compile(rf"^\s*{re.escape(t)}\s*$", re.I)
        for loc in (
            frame.get_by_role("option", name=name),
            frame.get_by_role("listitem", name=name),
            frame.locator(
                "ui5-li, ui5-li-suggestion-item, ui5-suggestion-list-item, ui5-list-item, ui5-option,"
                # Scoped tags (ui5-li-<suffix>) carry the unscoped name as an attribute.
                " [ui5-li], [ui5-li-suggestion-item], [ui5-suggestion-list-item], [ui5-list-item]"
            ).filter(has_text=name),
        ):
            try:
                n = await loc.count()
                for i in range(min(n, 5)):
                    if await loc.nth(i).is_visible():
                        return loc.nth(i)
            except PlaywrightError:
                continue
    return None


async def ui5wc_pick(frame, el, value):
    """SuccessFactors (ASSA ABLOY AI Scientist, 2026-10-14) State/Province/Region is a UI5 Web
    Components ``ui5-input`` with value help (aria-haspopup=dialog, placeholder "Select"):
    typed text is discarded unless a suggestion or value-help entry is picked. Type a prefix
    and pick the exact entry; else open the value help from its icon and pick there."""
    from .answers import US_STATES

    text = str(value).strip()
    targets = [text, US_STATES.get(text.upper(), "")] + [
        k for k, v in US_STATES.items() if v.lower() == text.lower()
    ]
    targets = sorted({t for t in targets if t}, key=len, reverse=True)
    for attempt in ("type", "icon"):
        await el.click(timeout=3000)
        await el.fill("", timeout=3000)
        if attempt == "type":
            await el.press_sequentially(targets[0][:4], delay=60)
        else:
            await el.evaluate(
                "e => { const h = e.getRootNode().host; const i = h && h.querySelector('[slot=icon]'); (i || h || e).click(); }"
            )
        pick = None
        for _ in range(16):
            await asyncio.sleep(0.25)
            if pick := await _ui5wc_exact(frame, targets):
                break
        if pick:
            await pick.click(timeout=3000)
            await asyncio.sleep(0.6)
            actual = await el.evaluate("e => e.getRootNode().host?.value || e.value || ''")
            if norm(actual) in {norm(t) for t in targets}:
                return actual
        try:
            await el.press("Escape", timeout=1000)
        except Exception:
            pass
    raise ValueError("The website did not commit the observed choice")


# Visible list entries anywhere, including shadow roots. With mark=true, entries already shown are
# marked (stale marks cleared first); otherwise the unmarked entries' texts are returned, i.e. those
# of the list that opened since.
UI5_CHOICES = """mark => {
  const found = [];
  const walk = root => {
    found.push(...root.querySelectorAll('[role=option],[role=listitem],[ui5-li],[ui5-li-suggestion-item]'));
    for (const n of root.querySelectorAll('*')) if (n.shadowRoot) walk(n.shadowRoot);
  };
  walk(document);
  const shown = n => { const r = n.getBoundingClientRect();
    return r.width > 0 && r.height > 0 && getComputedStyle(n).visibility !== 'hidden'; };
  // A UI5 list item's inner <li> reads only its hidden state text ("Not Selected"); its host has the entry.
  const inner = n => { const h = n.getRootNode().host;
    return !!h && (h.hasAttribute('ui5-li') || h.hasAttribute('ui5-li-suggestion-item')); };
  const texts = [];
  for (const n of found.filter(n => !inner(n))) {
    if (mark) { n.removeAttribute('data-jp-seen'); if (shown(n)) n.setAttribute('data-jp-seen', ''); continue; }
    if (n.hasAttribute('data-jp-seen') || !shown(n)) continue;
    const t = (n.innerText || n.textContent || '').replace(/\\s+/g, ' ').trim();
    if (t) texts.push(t);
  }
  return texts;
}"""


async def ui5wc_options(frame, el):
    """Entries of a SuccessFactors pick list (a UI5 Web Components value-help input): open its
    list from the input's icon, read the entries that appeared, and close it again."""
    await frame.evaluate(f"({UI5_CHOICES})(true)")
    texts = []
    for opener in ("icon", "keyboard"):
        if opener == "icon":
            await el.evaluate(
                "e => { const h = e.getRootNode().host; const i = h && h.querySelector('[slot=icon]'); (i || h || e).click(); }"
            )
        else:
            await el.click(timeout=3000)
            await el.press("F4")
        for _ in range(10):
            await asyncio.sleep(0.25)
            if texts := await frame.evaluate(f"({UI5_CHOICES})(false)"):
                break
        if texts:
            break
    try:
        await frame.page.keyboard.press("Escape")
        await asyncio.sleep(0.2)
        await el.evaluate("e => e.blur()")
    except PlaywrightError:
        pass
    return list(dict.fromkeys(t for t in texts if not placeholder_option(t)))


UI5_SELECTED = "e => (e.getRootNode().host?.selectedOption?.textContent || e.textContent || '').replace(/\\s+/g, ' ').trim()"


async def ui5wc_select_options(el):
    """A UI5 Web Components ``ui5-select``'s options are its light-DOM ``ui5-option`` children."""
    return await el.evaluate(
        "e => [...(e.getRootNode().host?.children || [])].filter(o => o.hasAttribute('ui5-option'))"
        ".map(o => o.textContent.replace(/\\s+/g, ' ').trim())"
    )


async def ui5wc_select(frame, el, value):
    """Pick an option of a ``ui5-select`` (SuccessFactors Gender, ASSA ABLOY 2026-10-15) the way a
    person does: open it and click the entry; keyboard steps are the fallback. Its own selectedOption
    is the readback."""
    options = [o for o in await ui5wc_select_options(el) if o and not placeholder_option(o)]
    target = best_option(options, value)
    if target is None:
        raise ValueError("No matching option is offered")
    if norm(await el.evaluate(UI5_SELECTED)) == norm(target):
        return target
    await el.click(timeout=3000)
    item = None
    for _ in range(12):
        await asyncio.sleep(0.25)
        if item := await _ui5wc_exact(frame, [target]):
            break
    if item:
        await item.click(timeout=3000)
        await asyncio.sleep(0.3)
    if norm(await el.evaluate(UI5_SELECTED)) != norm(target):
        # Closed select: arrow keys move the selection one option at a time and commit each step.
        try:
            await frame.page.keyboard.press("Escape")
        except PlaywrightError:
            pass
        await el.focus()
        everything = await ui5wc_select_options(el)
        for _ in range(len(everything)):
            current = norm(await el.evaluate(UI5_SELECTED))
            if current == norm(target):
                break
            here = next((i for i, o in enumerate(everything) if norm(o) == current), -1)
            want = next(i for i, o in enumerate(everything) if norm(o) == norm(target))
            await el.press("ArrowDown" if want > here else "ArrowUp")
            await asyncio.sleep(0.15)
    if norm(await el.evaluate(UI5_SELECTED)) == norm(target):
        return target
    raise ValueError("The website did not commit the observed choice")


UI5_RADIO_CHECKED = "e => e.checked === true || e.shadowRoot?.querySelector('[role=radio]')?.getAttribute('aria-checked') === 'true'"


async def ui5wc_radio(host):
    """Select a UI5 Web Components radio button (SuccessFactors Additional Information, ASSA ABLOY
    2026-10-15): a random point of a wide host missed its radio, so click the radio itself, then the
    host's leading edge, then Space on it. The employer re-renders the group after its handler runs,
    so the checked state is polled before the next way is tried."""
    inner = host.locator("[role=radio]").first
    for attempt in ("radio", "edge", "space"):
        try:
            if attempt == "radio":
                await inner.click(timeout=3000)
            elif attempt == "edge":
                box = await host.bounding_box()
                await host.click(timeout=3000, position={"x": 10, "y": (box["height"] / 2) if box else 10})
            else:
                await inner.focus(timeout=2000)
                await inner.press("Space")
        except (PlaywrightError, TypeError):
            continue
        for _ in range(10):
            if await host.evaluate(UI5_RADIO_CHECKED):
                return True
            await asyncio.sleep(0.2)
    return False


async def ui5_uncommitted(el, expected):
    """After blur, a UI5 text control that did not keep `expected` was cleared or flagged invalid."""
    if not norm(expected) or not await is_ui5_input(el):
        return False
    try:
        state = await el.evaluate(
            """e => {
            const wrap = e.closest('[class*="sapMInputBase"],[class*="sapMComboBox"]') || e;
            return {value: e.value || '', invalid: e.getAttribute('aria-invalid') === 'true' || /StateError/.test(wrap.className || '')};
        }""",
            timeout=1000,
        )
    except Exception:
        return False
    actual = norm(state["value"])
    return bool(
        state["invalid"] or not actual or (actual != norm(expected) and actual not in _alias_group(expected))
    )


# iCIMS's icimsDropdown (REA 2026-10-19, evidence ev_ab677d4c…: script/common/icimsDropdown.js) replaces each
# <select> with <a id="<select>_icimsDropdown" role=combobox> showing .dropdown-text, a container "<that id>_ctnr"
# holding input.dropdown-search, and ul#"<select>_dropdown-results" of li.dropdown-result[role=option] whose
# aria-label is the option text. The catalog is searched: a keyup in the search box runs a server (AJAX) search after
# 0.5-1.5 s, or filters the select's own options; rows render 25 at a time. A click on a row runs optionSelected
# (writes the select, collapses, then the field's onchange such as Country -> State). Reading the first 25 rows as
# the whole catalog made Country=India and State=Maryland "not offered" at Atlassian, and about 30 retries tripped
# iCIMS's CloudFront rate limit (403).
ICIMS_DROPDOWN = (
    "e=>e.tagName==='A'&&/_icimsDropdown$/.test(e.id||'')&&"
    "document.getElementById(e.id.slice(0,-14))?.tagName==='SELECT'?e.id.slice(0,-14):null"
)
ICIMS_ROWS = "li.dropdown-result[role=option]"
ICIMS_SELECT_DIRECT = """(e, text) => {
  const s = document.getElementById(e.id.slice(0, -14));
  const norm = t => (t || '').replace(/\\s+/g, ' ').trim();
  const option = [...s.options].find(o => !o.disabled && norm(o.text) === text);
  if (!option) return false;
  s.value = option.value;
  s.dispatchEvent(new Event('change', {bubbles: true}));
  const shown = e.querySelector('.dropdown-text');
  if (shown) shown.textContent = option.text;
  return s.selectedIndex >= 0 && norm(s.options[s.selectedIndex].text) === text;
}"""


async def icims_dropdown(el):
    """The <select> id behind an iCIMS search dropdown, or None for any other control."""
    try:
        return await el.evaluate(ICIMS_DROPDOWN)
    except PlaywrightError:
        return None


async def icims_select_texts(el):
    """The native select's real options: a static catalog lists them all; a server-searched one holds only its pick."""
    texts = await el.evaluate(
        "e=>[...document.getElementById(e.id.slice(0,-14)).options].filter(o=>!o.disabled)"
        ".map(o=>o.text.replace(/\\s+/g,' ').trim()).filter(Boolean)"
    )
    return [t for t in texts if not placeholder_option(t)]


def icims_text(label):
    """A row's text from its aria-label. iCIMS writes the label through ICIMS.escapeHtml, so "Master's Level Degree" is
    the attribute text "Master&#039;s Level Degree"; read raw, Emory's degrees never matched the applicant's, and a clicked
    row failed read-back against the select's own text (2026-10-25, 365 field errors in 25 runs)."""
    return re.sub(r"\s+", " ", html.unescape(str(label or ""))).strip()


async def icims_rows_now(frame, select_id):
    """The rows the dropdown shows right now, as (dropdown-index, text)."""
    found = await frame.locator(f'[id="{select_id}_dropdown-results"] {ICIMS_ROWS}').evaluate_all(
        "els=>els.filter(e=>e.getClientRects().length).map(e=>[e.getAttribute('dropdown-index'),"
        "(e.getAttribute('aria-label')||e.innerText||'').replace(/\\s+/g,' ').trim()])"
    )
    found = [(index, icims_text(text)) for index, text in found]
    return [(index, text) for index, text in found if text and not placeholder_option(text)]


async def icims_rows(frame, select_id, *, before=None, timeout=8.0):
    """The rows once they settle (unchanged for 0.5 s). After a search (`before` = the rows shown before typing)
    they must first change, or the widget must say there are no results: a server search answers 0.5-1.5 s after
    the last key plus the network, and reading the old first page as its answer made India "not offered"."""
    results = frame.locator(f'[id="{select_id}_dropdown-results"]')

    async def status():
        return await results.evaluate("u=>u.parentElement?.querySelector('[role=status]')?.innerText||''")

    # A list that already said "No Results" before the search (State until Country's value reaches it) must not end the
    # wait with that old status: State "found nothing" half a second after typing, on every tenant (2026-10-19).
    shown = await status() if before is not None else ""
    started, last, stable, changed = time.monotonic(), None, 0, before is None
    while time.monotonic() - started < timeout:
        found = await icims_rows_now(frame, select_id)
        if before is None and not found and not await status():
            # A server-searched list opens empty and fills when its first page answers: an empty list read twice was
            # taken as the catalog, and Emory's degree list read as having no choices (2026-10-25).
            await asyncio.sleep(0.25)
            continue
        if not changed:
            said = await status()
            empty = bool(re.search(r"no results|no matches", said, re.I))
            changed = found != before or (empty and (said != shown or time.monotonic() - started >= 1.6))
        stable = stable + 1 if found == last else 0
        last = found
        if changed and stable >= 2:
            break
        await asyncio.sleep(0.25)
    return last or []


async def icims_more_rows(frame, select_id, value, pages=10):
    """Scroll the open list to load further pages (25 rows each) until a row matches `value` or the list ends."""
    results = frame.locator(f'[id="{select_id}_dropdown-results"]')
    found = await icims_rows_now(frame, select_id)
    for _ in range(pages):
        if best_option([{"text": t, "disabled": False} for _, t in found], value) or len(found) < 20:
            break  # Matched, or a short page: the list has no further pages.
        await results.evaluate("u=>{u.scrollTop=u.scrollHeight;u.dispatchEvent(new Event('scroll'));}")
        more = await icims_rows(frame, select_id, before=found, timeout=4)
        if more == found:
            break
        found, added = more, len(more) - len(found)
        if added < 20:
            # A short page is the last one: scrolling again only waited 4 s for rows that never came, and a
            # degree spelled unlike every row paged Emory's list to the field deadline (2026-10-25).
            break
    return found


async def icims_dropdown_commit(
    frame,
    el,
    value,
    choose=None,
    resolve=None,
    school_scope=False,
    employer_scope=False,
    search_queries=None,
    thorough=True,
    broad_queries=None,
):
    """Pick through iCIMS's own dropdown UI: open, search, click the row. None when the control is not one.

    An optional field (thorough=False) gets one search and no paging: every keystroke pause and every scrolled page is
    a server request, and failed optional Country pickers (several searches and up to ten pages each) preceded both
    CloudFront 403s on Atlassian's "Submit Profile" (2026-10-19)."""
    select_id = await icims_dropdown(el)
    if not select_id:
        return None
    static = await icims_select_texts(el)
    if len(static) > 1:
        # A static catalog is all in the native select. Searching it through the widget ran out of the field budget at
        # Emory ("highest level of education", three runs, 2026-10-24); the select takes the choice directly and its
        # change event runs the field's onchange as a row click does.
        eligible = [{"text": t, "disabled": False} for t in static if not school_scope or school_option_matches(value, t)]
        picked = (best_option(eligible, value) or {}).get("text")
        if not picked and resolve:
            picked = resolve(static)
        if not picked and choose and not employer_scope:
            picked = await choose(static)
        if picked in static and await el.evaluate(ICIMS_SELECT_DIRECT, picked):
            await asyncio.sleep(0.3)  # Dependent fields (Country -> State) rebuild after the change.
            return picked
    container = frame.locator(f'[id="{select_id}_icimsDropdown_ctnr"]')
    box = container.locator("input.dropdown-search")
    # Stay inside the form's 12 s field write budget: a deadline there leaves the field neither set nor omitted.
    ends = time.monotonic() + (10 if thorough else 6)

    def left():
        return ends - time.monotonic()

    async def searchable():
        return bool(await box.count()) and await box.evaluate(
            "b=>!b.classList.contains('dropdown-invisible')&&b.getAttribute('aria-hidden')!=='true'"
        )

    async def rows_for(query):
        if not await container.evaluate("c=>!c.classList.contains('dropdown-invisible')&&c.getClientRects().length>0"):
            await el.click(timeout=3000)
            await asyncio.sleep(0.3)
        if query is not None and await searchable():
            before = await icims_rows_now(frame, select_id)
            await box.fill("", timeout=2000)
            await box.press_sequentially(str(query)[:40], delay=35, timeout=6000)
            found = await icims_rows(frame, select_id, before=before, timeout=max(1.5, min(8, left() - 1)))
        else:
            found = await icims_rows(frame, select_id, timeout=max(1.5, min(8, left() - 1)))
        # Lists without a search box, and searches that answer a page at a time, load more rows on scroll.
        pages = int(max(0, left() - 1) // 1.5) if thorough else 0
        return await icims_more_rows(frame, select_id, value, pages=min(10, pages)) if found and pages else found

    # Searches whose results hold the value's own rows (a degree's level) go first: "Master of Science" found nothing in
    # Emory's catalog, whose row is "Master's Level Degree", and each empty search cost the field 2-4 s (2026-10-25).
    queries = list(broad_queries or []) + [str(value)] + list(search_queries or [])
    queries += sorted(_alias_group(value) - {norm(value)}, key=len, reverse=True)
    if len(str(value).split()) > 1:
        queries.append(str(value).split()[0])
    choice, seen, seen_query = None, [], None
    for query in list(dict.fromkeys(q for q in queries if q))[: 3 if thorough else 1]:
        if seen and left() < 2.5:
            break
        found = await rows_for(query)
        if not found and not seen and left() > 3:
            # Committing Country runs iCIMS's change handler, which rebuilds the dependent State list asynchronously;
            # searched at once, State answered nothing and failed its first commit on every tenant (2026-10-19).
            await asyncio.sleep(1.5)
            found = await rows_for(query)
        if found:
            seen, seen_query = found, query
        options = [{"text": text, "disabled": False, "index": index} for index, text in found]
        eligible = [o for o in options if not school_scope or school_option_matches(value, o["text"])]
        choice = best_option(eligible, value)
        if not choice and resolve and eligible and (picked := resolve([o["text"] for o in eligible])):
            # The rows on screen hold the value's equivalent: pick it now, before another search replaces them.
            choice = next((o for o in eligible if o["text"] == picked), None)
        if choice or not await searchable():
            break  # A list without a search box shows the same rows for every query.
    options = [{"text": text, "disabled": False, "index": index} for index, text in seen]
    if not choice and resolve and options:
        picked = resolve([o["text"] for o in options])
        choice = next((o for o in options if o["text"] == picked), None) if picked else None
    if not choice and choose and options and not employer_scope:
        selected = await choose([o["text"] for o in options])
        choice = next((o for o in options if o["text"] == selected), None)
    if choice and await icims_rows_now(frame, select_id) != seen:
        # The rows shown are a later search's: an index from the earlier results would click another row, or none.
        shown = await rows_for(seen_query)
        choice = next(({"text": t, "index": i} for i, t in shown if t == choice["text"]), None)
    if not choice and (school_scope or employer_scope):
        other = school_other_option if school_scope else employer_other_option
        others = [
            {"text": text, "disabled": False, "index": index}
            for index, text in await rows_for("Other")
            if other(text)
        ]
        choice = others[0] if len(others) == 1 else None
    if not choice:
        await el.press("Escape")
        raise ValueError("No matching option is offered")
    await frame.locator(
        f'[id="{select_id}_dropdown-results"] {ICIMS_ROWS}[dropdown-index="{choice["index"]}"]'
    ).click(timeout=3000)
    for _ in range(12):
        shown, picked = await el.evaluate(
            "e=>[(e.querySelector('.dropdown-text')||e).innerText.replace(/\\s+/g,' ').trim(),"
            "(s=>s.selectedIndex>=0?s.options[s.selectedIndex].text.replace(/\\s+/g,' ').trim():'')"
            "(document.getElementById(e.id.slice(0,-14)))]"
        )
        if norm(picked) == norm(choice["text"]) or norm(choice["text"]) in norm(shown):
            await asyncio.sleep(0.3)  # The field's onchange (Country -> State) runs after the click.
            return choice["text"]
        await asyncio.sleep(0.25)
    raise ValueError("The website did not commit the observed choice")


# SuccessFactors' classic application keeps sections closed (XPO 2026-10-22: Profile Information, Job-Specific
# Information); their required pickers were scanned but hidden and every write timed out, in the deterministic flow
# and the guided agent alike.
OPEN_RCM_SECTIONS = """() => {
  const bars = [...document.querySelectorAll('button.rcmFormSectionTopBar[aria-expanded="false"]')]
    .filter(b => b.getClientRects().length);
  bars.forEach(b => b.click());
  return bars.length;
}"""


async def open_collapsed_sections(page):
    """Open closed SuccessFactors form sections in every frame; the number opened."""
    opened = 0
    for frame in page.frames[:12]:
        try:
            opened += int(await asyncio.wait_for(frame.evaluate(OPEN_RCM_SECTIONS), 2) or 0)
        except Exception:
            continue
    if opened:
        await asyncio.sleep(0.5)
    return opened


# iCIMS's email step asks a phone country code with the same dropdown look but no <select> (Emory, Stifel
# 2026-10-21/22): <a class="dropdown-select" role=combobox aria-controls="dropdownOptions">, whose container holds
# input.dropdown-search (filtered on input) and ul.dropdown-results of li[role=option] labelled "(+1) United States".
# The scan labels the hidden search box "Phone Country Code"; clicking it timed out and typing into the anchor was
# "Value not accepted".
ICIMS_CODE_DROPDOWN = """e => {
  let ctnr = null, anchor = null;
  if (e.tagName === 'A' && e.classList.contains('dropdown-select')) {
    anchor = e; ctnr = document.getElementById(e.getAttribute('aria-controls') || '');
  } else if (e.classList.contains('dropdown-search')) {
    ctnr = e.closest('.dropdown-container');
    anchor = ctnr && ctnr.id ? document.querySelector('a.dropdown-select[aria-controls="' + ctnr.id + '"]') : null;
  }
  if (!ctnr || !anchor || !anchor.id || /_icimsDropdown$/.test(anchor.id) || !ctnr.querySelector('ul.dropdown-results'))
    return null;
  return [anchor.id, ctnr.id];
}"""
DIAL_PREFIX = re.compile(r"^\s*\(\+?\d+\)\s*")
DIAL_CODE = re.compile(r"\(?\+\d{1,4}\)?")


async def icims_code_dropdown_commit(frame, el, value):
    """Pick a row of iCIMS's select-less dropdown by its name; None when the control is not one."""
    try:
        ids = await el.evaluate(ICIMS_CODE_DROPDOWN)
    except PlaywrightError:
        return None
    if not ids:
        return None
    anchor, container = frame.locator(f'[id="{ids[0]}"]'), frame.locator(f'[id="{ids[1]}"]')
    # A saved code reads "+1 (United States)", "(+1) United States" or "+1": the country name and the dial code
    # are matched separately. Planview (2026-10-26) searched the whole "+1 (United States)" and found no row.
    raw = str(value)
    code = re.search(r"\+\s?(\d{1,4})(?!\d)", raw)
    digits = code[1] if code else (re.sub(r"\D", "", raw) if re.fullmatch(r"\s*\d{1,4}\s*", raw) else "")
    wanted = " ".join(re.sub(r"[()]", " ", DIAL_CODE.sub(" ", raw)).split())
    if not await container.is_visible():
        await anchor.click(timeout=3000)
        await asyncio.sleep(0.3)

    async def visible_rows():
        return await container.locator("ul.dropdown-results li[role=option]").evaluate_all(
            "els=>els.filter(e=>e.getClientRects().length).map(e=>[e.id,"
            "(e.getAttribute('aria-label')||e.innerText||'').replace(/\\s+/g,' ').trim()])"
        )

    def name(row):
        # "(+1) United States", "United States (+1)" and "United States +1" name one country. Planview's email step
        # (2026-10-26) stopped at "No matching option is offered" on every pass; only the prefix form was read.
        return norm(DIAL_CODE.sub(" ", row))

    want = norm(wanted)
    same = next((group for group in ALIASES if want in group), {want})

    def matching(rows):
        return [r for r in rows if r[0] and name(r[1]) == want] or [r for r in rows if r[0] and name(r[1]) in same]

    # The whole list is rendered (REA, Planview 2026-10-27: 245 rows, "(+1) United States"); typing the saved
    # "United States of America" into its search box filtered that row out. Search only when the list holds no match.
    rows = await visible_rows()
    named = matching(rows)
    box = container.locator("input.dropdown-search")
    if len(named) != 1 and await box.count() and await box.is_visible() and wanted and not wanted.isdigit():
        for query in dict.fromkeys([wanted, *sorted(same, key=len, reverse=True)]):
            await box.fill(str(query)[:30], timeout=2000)
            await asyncio.sleep(0.6)
            rows = await visible_rows()
            if len(named := matching(rows)) == 1:
                break
    if digits and len(named) != 1:
        coded = [r for r in rows if r[0] and re.search(rf"\+{digits}(?!\d)", r[1])]
        # +1 is shared by the North American plan; the code alone means the United States.
        named = coded if len(coded) == 1 else [r for r in coded if name(r[1]) in ALIASES[0]]
    if len(named) != 1:
        await anchor.press("Escape")
        raise ValueError("No matching option is offered")
    await frame.locator(f'[id="{named[0][0]}"]').click(timeout=3000)
    await asyncio.sleep(0.3)
    shown = norm(DIAL_CODE.sub(" ", await anchor.inner_text()))
    if name(named[0][1]) in shown:
        return named[0][1]
    raise ValueError("The website did not commit the observed choice")


async def combobox(
    frame,
    el,
    value,
    options_hint=None,
    choose=None,
    multi=False,
    prefer_catalog=False,
    search_queries=None,
    school_scope=False,
    resolve=None,
    employer_scope=False,
    thorough=True,
    broad_queries=None,
):
    """ARIA combobox: input or div. Types to search when editable, then commits a real option.

    resolve(texts) may pick one offered text deterministically (a location search
    returns the same city in several states; only the applicant's address decides).
    """
    await dismiss_pickers(frame)
    picked = await icims_dropdown_commit(
        frame,
        el,
        value,
        choose=choose,
        resolve=resolve,
        school_scope=school_scope,
        employer_scope=employer_scope,
        search_queries=search_queries,
        thorough=thorough,
        broad_queries=broad_queries,
    )
    if picked is not None:
        return picked
    picked = await icims_code_dropdown_commit(frame, el, value)
    if picked is not None:
        return picked
    # Greenhouse "mark all that apply" questions are react-select multi
    # comboboxes: the commit is a chip, never a single-value or input value.
    multi = multi or await el.evaluate(
        "e => !!e.closest('.select__control')?.querySelector('.select__value-container--is-multi')"
    )
    if await el.evaluate("e => e.classList.contains('rcmpaginatedselectinput')"):
        if employer_scope:
            # XPO (2026-10-22) lists freight carriers and "Other", then asks the name of an employer listed as Other.
            # The applicant's employer is taken when the full list offers it, else Other; two typed searches and
            # the full list for an unlisted employer outran the 12 s field budget on every run.
            try:
                return await _rcm_paginated(frame, el, value, full_list_only=True)
            except ValueError:
                return await _rcm_paginated(frame, el, "Other")
        return await _rcm_paginated(frame, el, value, choose=choose)
    await open_combobox(el)
    if await _is_ui5_picker(frame, el):
        return await _ui5_combobox(frame, el, value)
    editable = await el.evaluate(
        "e => ['INPUT','TEXTAREA'].includes(e.tagName) && !e.readOnly && !e.disabled"
    )
    scope = await _popup_scope(el)
    opts = []
    choice = None

    def eligible(options):
        return [
            o
            for o in options
            if not o["disabled"] and (not school_scope or school_option_matches(value, o["text"]))
        ]

    if editable:
        listed = await wait_options(frame, scope, timeout=0.4, owner=el)
        choice = best_option(eligible(listed), value)
        if not choice and choose and listed and prefer_catalog:
            # A fixed select with a search input can offer a valid category
            # (Job Board for LinkedIn). Typing the desired leaf first hides it.
            # Only a known category-type question opts in; school/city catalogs
            # must search the confirmed value before asking about unrelated rows.
            real = eligible(listed)
            selected = await choose([o["text"] for o in real])
            choice = next((o for o in real if o["text"] == selected), None)
        typed = False
        if not choice:
            await el.fill("")
            # Search widgets may listen to keyup instead of input (Oracle address fields).
            await el.press_sequentially(str(value), delay=12, timeout=4000)
            typed = True
        opts = (
            listed
            if choice
            else await wait_options(
                frame,
                scope,
                timeout=4,
                query=value if typed else None,
                owner=el,
                # The list shown before the search is not its answer. A search result that has stayed put for
                # more than half a second is, even when no row repeats the query ("Rivertown, MD" -> "Rivertown,
                # Maryland, United States"); a menu that keeps saying "No options" is the answer too.
                changed_from=[o["text"] for o in listed] if typed else None,
                accept=(lambda rows: resolve([o["text"] for o in eligible(rows)])) if resolve else None,
                stable_for=0.6,
                empty_after=1.2 if typed else None,
            )
        )
    else:
        opts = await wait_options(frame, scope, timeout=3, owner=el)
    scope = await _popup_scope(el) or scope
    choice = choice or best_option(eligible(opts), value)
    if not choice and resolve and opts:
        picked = resolve([o["text"] for o in eligible(opts)])
        choice = next((o for o in eligible(opts) if o["text"] == picked), None) if picked else None
    if not choice and not editable:
        choice = await scroll_for_option(frame, value, scope)
    if not choice and editable:
        # Oracle searches literal display text: "United States of America" yields no
        # rows when the employer names it "United States". Search known equivalents.
        aliases = list(
            dict.fromkeys(
                (search_queries or []) + sorted(_alias_group(value) - {norm(value)}, key=len, reverse=True)
            )
        )[:3]
        for alias in aliases:
            await el.fill("")
            await el.press_sequentially(alias, delay=12, timeout=4000)
            opts = await wait_options(frame, scope, timeout=2, query=alias, owner=el)
            choice = best_option(eligible(opts), value)
            if choice:
                break
    if not choice and choose and opts and not employer_scope:
        real = [
            o
            for o in eligible(opts)
            if not re.search(r"no (results|matches|options)|not found", o["text"], re.I)
        ]
        selected = await choose([o["text"] for o in real]) if real else None
        choice = next((o for o in real if o["text"] == selected), None)
    if not choice and school_scope:
        # Search the actual institution first. Its absence is a reason to use
        # the applicant's explicit Other preference, never another institution.
        if editable:
            await el.fill("")
            await el.press_sequentially("Other", delay=12, timeout=4000)
            opts = await wait_options(frame, scope, timeout=2, query="Other", owner=el)
        others = [o for o in opts if not o["disabled"] and school_other_option(o["text"])]
        if len(others) == 1:
            choice = others[0]
    if not choice and employer_scope:
        # A fixed employer list without the applicant's employer (Goldman Sachs' Oracle "Name of Latest Employer",
        # 2026-10-18). Only its own Other / not listed choice is truthful; a similar name never is.
        if editable:
            await el.fill("")
            await el.press_sequentially("Other", delay=12, timeout=4000)
            opts = await wait_options(frame, scope, timeout=2, query="Other", owner=el)
        others = [o for o in opts if not o["disabled"] and employer_other_option(o["text"])]
        if len(others) == 1:
            choice = others[0]
    if not choice and not editable and not school_scope and not employer_scope:
        await frame.page.keyboard.type(str(value)[:24], delay=12)
        opts = await wait_options(frame, scope, timeout=1.5, owner=el)
        choice = best_option(opts, value)
    if not choice:
        await el.press("Escape")
        raise ValueError("No matching option is offered")
    await click_option(frame, choice, owner=el)
    await asyncio.sleep(0.1)
    if multi:
        await close_picker(el, frame)
        selected = await el.evaluate("""e=>{
            if(e.matches('input.cx-multi-select-input')) {
                const root=e.closest('.cx-multi-select-container')?.parentElement;
                if(root?.querySelectorAll('input.cx-multi-select-input').length!==1)return '';
                return [...root.querySelectorAll('.cx-multi-select-pill__value-text')]
                    .map(x=>x.textContent.trim()).filter(Boolean).join('\\n');
            }
            let h=e;while(h&&h.tagName!=='SPL-MULTISELECT-AUTOCOMPLETE')h=h.parentElement||h.getRootNode()?.host;
            const react=e.closest('.select__control');
            if(react && react.querySelectorAll('[role=combobox]').length===1)
                return [...react.querySelectorAll('.select__multi-value__label')]
                    .filter(n=>n.getClientRects().length).map(n=>n.textContent.trim()).join('\\n');
            const values=new Set(Array.isArray(h?.value)?h.value:[]);
            return [...(h?.shadowRoot?.querySelectorAll('spl-select-option[value]')||[])]
                .filter(c=>values.has(c.getAttribute('value'))).map(c=>c.textContent.trim()).join('\\n');
        }""")
        if norm(choice["text"]) not in {norm(v) for v in selected.splitlines()}:
            raise ValueError("Multiselect did not retain the selected option")
        return selected
    deadline = time.monotonic() + 2
    while time.monotonic() < deadline:
        if not await el.count():
            return choice["text"]  # The form owner rematches and verifies the replacement control.
        actual = (
            await el.evaluate("""e=>{
            const root=e.closest('.select__control');
            const chosen=root?.querySelectorAll('.select__single-value');
            if(root?.querySelectorAll('[role=combobox]').length===1 && chosen?.length===1
                && chosen[0].getClientRects().length)return chosen[0].textContent.trim();
            return e.value || '';
        }""")
            if editable
            else await el.evaluate("""e=>{
            const content=n=>{
                if(n.nodeType===Node.TEXT_NODE)return n.textContent||'';
                if(n.nodeType!==Node.ELEMENT_NODE)return '';
                const assigned=n.tagName==='SLOT'?n.assignedNodes({flatten:true}):[];
                return [...(assigned.length?assigned:n.shadowRoot?n.shadowRoot.childNodes:n.childNodes)].map(content).join(' ');
            };return content(e).replace(/\\s+/g,' ').trim();
        }""")
        )
        prefix = re.sub(r"\+\s+(?=\d)", "+", actual)
        phone_code = re.fullmatch(r"\+\d+", prefix) and re.search(
            r"(?<!\d)\+\s*" + re.escape(prefix[1:]) + r"(?!\d)", choice["text"]
        )
        accepted = (
            norm(actual) in _alias_group(choice["text"])
            or norm(actual) == norm(choice["text"].split(",")[0])
            or (not editable and norm(choice["text"]) in norm(actual))
            or phone_code
        )
        if accepted and not await owned_picker_open(el):
            return actual if editable else choice["text"]
        await asyncio.sleep(0.1)
    raise ValueError("The website did not commit the observed choice")


async def lever_location(frame, el, value, address, choose):
    """Lever's location lookup has no ARIA roles and needs real typing plus a selected-place id."""
    query = address.city or str(value)
    await el.fill("")
    await el.press_sequentially(query, delay=15, timeout=4000)
    opts = await wait_options(frame, None, timeout=5, selector=".dropdown-results .dropdown-location")
    matches = []
    for option in opts:
        parts = [norm(p) for p in option["text"].split(",")]
        if (
            len(parts) == 3
            and address.city
            and address.state
            and address.country
            and (
                parts[0] == norm(address.city)
                and parts[1] in _alias_group(address.state)
                and parts[2] in _alias_group(address.country)
            )
        ):
            matches.append(option)
    choice = matches[0] if len(matches) == 1 else best_option(opts, value)
    if not choice and opts:
        selected = await choose([o["text"] for o in opts])
        choice = next((o for o in opts if o["text"] == selected), None)
    if not choice:
        raise ValueError("No offered location matches the confirmed address")
    await click_option(frame, choice)
    await asyncio.sleep(0.1)
    accepted = await el.evaluate("e=>!!e.parentElement.querySelector('input[name=selectedLocation]')?.value")
    if not accepted:
        raise ValueError("The website did not commit the selected location")
    return await el.input_value()


async def open_prompt(container):
    """The selected-pill overlay can cover the input. Open the observed prompt icon first."""
    inp = container.locator("input:not([type=hidden])").first
    icon = container.locator('[data-automation-id="promptIcon"]').first
    if await icon.count() and await icon.is_visible():
        await field_click(icon, timeout=1500)
    else:
        try:
            await field_click(inp, timeout=1500)
        except Exception as exc:
            if interaction_failure(exc) != "covered":
                raise
            await field_click(container, timeout=1500)
    await inp.focus(timeout=2000)
    return inp


async def owned_picker_open(el):
    """Check explicit popup ownership without changing the menu or its query."""
    return await el.evaluate("""e => {
      if(e.getAttribute('aria-expanded') === 'true') return true;
      const selected = '[data-automation-id="selectedItem"],[data-automation-id="selectedItemList"],[aria-label="items selected"]';
      const shown = n => n && n.getClientRects().length &&
        getComputedStyle(n).visibility !== 'hidden' && !n.closest(selected);
      const reference = e.getAttribute('aria-controls') || e.getAttribute('aria-owns') ||
        e.querySelector('[aria-controls]')?.getAttribute('aria-controls');
      if (reference && reference.split(/\\s+/).some(id => shown(e.getRootNode().getElementById?.(id) || document.getElementById(id))))
        return true;
      if (e.matches('a[id$="_icimsDropdown"][role=combobox]') &&
          shown(document.getElementById(e.id + '_ctnr'))) return true;
      const prompt = e.closest('[data-automation-id="multiSelectContainer"]');
      return !!prompt && [...prompt.querySelectorAll(
        '[role=listbox]:not([data-automation-id="selectedItemList"]):not([aria-label="items selected"]),[role=menu],[data-automation-id=promptOption]'
      )].some(shown);
    }""")


async def open_owned_picker(frame, el, kind):
    """One shared opener for deterministic, navigation and recovery routes."""
    if kind in {"radio", "radiogroup", "pills", "checkboxgroup", "select", "select-multiple"}:
        return el  # Native choices have no DOM popup; inspection is read-only.
    await dismiss_pickers(frame)
    if kind == "prompt":
        return await open_prompt(el)
    if kind == "combobox":
        await open_combobox(el)
        return el
    await field_click(el, timeout=2000)
    return el


async def back_menu(frame, owner):
    scope = await _popup_scope(owner)
    if not scope:
        raise ValueError("The menu has no observed owner")
    menu = frame.locator("#" + scope.replace('"', ""))
    back = menu.locator('[data-automation-id="backButton"],[aria-label="Back"]')
    if not await back.count():
        back = menu.get_by_role("button", name=re.compile(r"^back(?: to .+)?$", re.I))
    shown = [b for b in await back.all() if await b.is_visible()]
    if len(shown) != 1:
        raise ValueError("An owned menu Back control is unavailable")
    await field_click(shown[0], timeout=2000)


async def scroll_menu(frame, owner, direction="down"):
    scope = await _popup_scope(owner)
    if not scope:
        raise ValueError("The menu has no observed owner")
    opts = await visible_options(frame, scope)
    if not opts:
        raise ValueError("An owned menu option is unavailable")
    return await frame.locator(f'[data-jp-opt="{opts[0]["id"]}"]').evaluate(
        """(e,direction)=>{let n=e;for(let i=0;i<10&&n&&n.tagName!=='BODY';i++,n=n.parentElement){
          if(n.scrollHeight>n.clientHeight+4 && /auto|scroll/.test(getComputedStyle(n).overflowY)){
            const before=n.scrollTop;n.scrollTop+=(direction==='up'?-1:1)*Math.max(40,n.clientHeight*.8);
            return n.scrollTop!==before;
          }}return false;}""",
        direction,
    )


async def shown_prompt_options(frame, container):
    """The option texts a prompt shows right now (its catalog or a previous search), before a new search."""
    try:
        scope = await _popup_scope(container)
        return [o["text"] for o in await visible_options(frame, scope, "[data-automation-id=promptOption],[role=option]")]
    except Exception:
        return []


def school_head(value):
    """The institution part of a school name: "University of Michigan, Ann Arbor" -> "University of Michigan"."""
    return re.split(r"\s*[,\u2013\u2014]\s*|\s+-\s+", str(value))[0].strip() or str(value)


def prompt_fallback_queries(value, school_scope=False):
    """Shorter searches after an exact prompt search returns nothing."""
    words = str(value).split()
    if not school_scope:
        return [words[0]] if len(words) > 1 else []
    # "University of Michigan, Ann Arbor" -> "University of Michigan" -> "Michigan".
    head = re.split(r"\s*[,\u2013\u2014]\s*|\s+-\s+", str(value))[0].strip()
    generic = {
        "university",
        *SCHOOL_ABBREVIATIONS,
        "of",
        "the",
        "college",
        "institute",
        "school",
        "and",
        "at",
        "state",
        "technology",
    }
    distinctive = [w for w in re.findall(r"[A-Za-z]+", str(value)) if w.lower() not in generic and len(w) > 2]
    # Last: a list without the school offers "Other" only when searched (Lennar, Nanyang Technological University).
    # school_option_matches commits nothing else from that search.
    return [q for q in dict.fromkeys([head, *distinctive[:1], "Other"]) if q and q != str(value)]


PILLS_JS = """e=>{
    const root=e.closest('[data-automation-id^="formField-"],[data-automation-id="multiSelectContainer"]') || e;
    return [...root.querySelectorAll('[data-automation-id="selectedItem"]')].map(n=>n.innerText.trim()).filter(Boolean);
}"""


async def prompt_pills(container):
    return await container.evaluate(PILLS_JS)


async def remove_prompt_pill(container, text):
    """Delete one selected item this call did not choose (its own DELETE_charm)."""
    try:
        await container.evaluate(
            """(e,text)=>{
              const root=e.closest('[data-automation-id^="formField-"],[data-automation-id="multiSelectContainer"]') || e;
              const pill=[...root.querySelectorAll('[data-automation-id="selectedItem"]')]
                .find(n=>n.innerText.trim()===text);
              pill?.querySelector('[data-automation-id="DELETE_charm"]')?.click();
            }""",
            text,
            timeout=1500,
        )
    except PlaywrightError:
        pass


async def commit_prompt_name(frame, container, target):
    """Commit an exact catalog name: one search for it, then its pill. Workday selects a search's sole result by
    itself, so any other pill this search added is removed again (REA, 2026-10-25)."""
    before = await prompt_pills(container)
    if any(norm(text) == norm(target) for text in before):
        return next(text for text in before if norm(text) == norm(target))
    await dismiss_pickers(frame)
    inp = await open_prompt(container)
    await inp.fill("", timeout=4000)
    await inp.press_sequentially(target, delay=10)
    await inp.press("Enter")
    deadline = time.monotonic() + 4
    while time.monotonic() < deadline:
        now = await prompt_pills(container)
        if any(norm(text) == norm(target) for text in now if text not in before):
            break  # The sole result committed itself.
        opts = await visible_options(
            frame, await _popup_scope(container), "[data-automation-id=promptOption],[role=option]"
        )
        exact = [o for o in opts if o["leaf"] and not o["disabled"] and norm(o["text"]) == norm(target)]
        if len(exact) == 1:
            await click_option(frame, exact[0], owner=container)
            break
        if not opts and await menu_notice(frame) == "none":
            break
        await asyncio.sleep(0.08)
    await asyncio.sleep(0.2)
    now = await prompt_pills(container)
    for stray in [text for text in now if text not in before and norm(text) != norm(target)]:
        await remove_prompt_pill(container, stray)
    try:
        await inp.fill("", timeout=1000)  # Text left in the box is searched again on blur.
    except PlaywrightError:
        pass
    await dismiss_pickers(frame)
    committed = next((text for text in await prompt_pills(container) if norm(text) == norm(target)), None)
    if not committed:
        raise ValueError("No matching option is offered")
    return committed


async def prompt(frame, container, value, choose=None, school_scope=False, catalog=None):
    """Workday multiselect prompt: search, then commit a leaf; categories are opened once.

    `value` may list candidates in preference order (the recorded major, then the profile's fallback). With the page's
    own full value list (`catalog`), the answer is chosen from it first and only its exact name is searched: probe
    searches committed whatever single result they found ("Data" -> "Data Processing")."""
    candidates = [str(v) for v in value] if isinstance(value, (list, tuple)) else [str(value)]
    if catalog:
        names = [name for name in dict.fromkeys(catalog) if name and not placeholder_option(name)]
        target = None
        for want in candidates:
            pool = [name for name in names if not school_scope or school_option_matches(want, name)]
            if target := best_option(pool, want):
                break
        if not target and choose:
            picked = await choose(names)
            target = picked if picked in names else None
        if not target:
            error = ValueError("No matching option is offered")
            error.offered = {"catalog": names[:8], "candidates": candidates}
            raise error
        return await commit_prompt_name(frame, container, target)
    value = candidates[0]
    interpretations = 0
    # What each search returned (the employer's own option labels), for the failure record.
    seen = {}

    async def supported_leaf(options):
        nonlocal interpretations
        leaves = [
            option
            for option in options
            if option["leaf"]
            and not option["disabled"]
            and (not school_scope or school_option_matches(value, option["text"]))
        ]
        choice = best_option(leaves, value)
        if not choice and leaves and choose and interpretations < 2:
            interpretations += 1
            selected = await choose(list(dict.fromkeys(option["text"] for option in leaves)))
            choice = next((option for option in leaves if option["text"] == selected), None)
        return choice

    selected = await container.evaluate("""e=>{
        const root=e.closest('[data-automation-id^="formField-"]') || e;
        return [...root.querySelectorAll('[data-automation-id="selectedItem"]')]
            .filter(n=>n.getClientRects().length).map(n=>n.innerText.trim()).filter(Boolean);
    }""")

    async def committed_search_leaf():
        # Workday can commit Enter directly to a matching catalog pill and
        # reopen the alphabetical list. That list is not the search result.
        current = await container.evaluate("""e=>{
            const root=e.closest('[data-automation-id^="formField-"]') || e;
            return [...root.querySelectorAll('[data-automation-id="selectedItem"]')]
                .map(n=>n.innerText.trim()).filter(Boolean);
        }""")
        fresh = [text for text in current if text not in selected]
        if not fresh:
            return None
        match = None
        if school_scope:
            match = next((text for text in fresh if school_option_matches(value, text)), None)
        elif not (match := best_option(fresh, value)) and choose:
            match = await choose(fresh)
            match = match if match in fresh else None
        if match:
            return match
        # Workday committed a search's sole result the policy rejects ("Data" -> "Data Processing"): never leave it.
        for stray in fresh:
            await remove_prompt_pill(container, stray)
        return None

    if school_scope:
        if len(selected) == 1 and school_option_matches(value, selected[0]):
            return selected[0]
    elif committed := best_option(selected, value):
        return committed  # An already selected leaf does not need another menu search.
    elif selected and choose:
        # Interpret the employer's already committed taxonomy before replacing
        # it with the profile's original wording and reopening an empty search.
        committed = await choose(selected)
        if committed in selected:
            return committed
    await dismiss_pickers(frame)
    inp = await open_prompt(container)
    # Workday school lists name the institution ("University of Michigan"): searching the profile's
    # "University of Michigan, Ann Arbor" never matched and waited out the result wait before the
    # fallback. school_option_matches still decides which campus may be committed.
    first = school_head(value) if school_scope else str(value)
    await inp.fill("", timeout=4000)
    before = await shown_prompt_options(frame, container)
    await inp.press_sequentially(first, delay=10)
    await inp.press("Enter")
    # Free-entry skill prompts commit Enter straight into a tag and offer no
    # menu at all. Use the website's selected item as evidence of that commit.
    accepted = await container.evaluate(
        """(e,value) => {
      const root=e.closest('[data-automation-id^="formField-"],[data-automation-id="multiSelectContainer"]') || e.parentElement;
      const norm=t=>(t||'').replace(/\\s+/g,' ').trim().toLowerCase();
      return [...(root?.querySelectorAll('[data-automation-id="selectedItem"]')||[])]
        .map(n=>n.innerText.trim()).find(t=>norm(t)===norm(value)) || null;
    }""",
        str(value),
    )
    if accepted:
        return accepted
    opts = await wait_options(
        frame,
        None,
        timeout=4,
        selector="[data-automation-id=promptOption],[role=option]",
        query=first,
        owner=container,
        require_query_match=True,
        changed_from=before,
        empty_after=0.3,
    )
    seen[first] = [o["text"] for o in opts][:8]
    if committed := await committed_search_leaf():
        await dismiss_pickers(frame)
        return committed
    real = [o for o in opts if not re.search(r"^no items|no results|no matches", o["text"], re.I)]
    choice = await supported_leaf(real) if real else None
    fallbacks = [query for query in prompt_fallback_queries(value, school_scope) if query != first]
    for query in [] if choice else fallbacks:
        # Employer taxonomies may say "Management Information Systems" where
        # the resume says "Information Systems", or "University of Michigan -
        # Ann Arbor" where it says "..., Ann Arbor". Search a shorter term,
        # then select only from actual results using the original applicant context.
        await inp.fill("")
        before = await shown_prompt_options(frame, container)
        await inp.press_sequentially(query, delay=10)
        await inp.press("Enter")
        opts = await wait_options(
            frame,
            None,
            timeout=4,
            selector="[data-automation-id=promptOption],[role=option]",
            query=query,
            owner=container,
            require_query_match=True,
            changed_from=before,
            empty_after=0.3,
        )
        seen[query] = [o["text"] for o in opts][:8]
        if committed := await committed_search_leaf():
            await dismiss_pickers(frame)
            return committed
        real = [o for o in opts if not re.search(r"^no items|no results|no matches", o["text"], re.I)]
        if choice := await supported_leaf(real):
            break
    if not choice and school_scope:
        # School prompts use the same applicant preference as comboboxes:
        # search the real institution first, then an offered Other fallback.
        await inp.fill("")
        await inp.press_sequentially("Other", delay=10)
        await inp.press("Enter")
        opts = await wait_options(
            frame,
            None,
            timeout=3,
            selector="[data-automation-id=promptOption],[role=option]",
            query="Other",
            owner=container,
        )
        others = [o for o in opts if o["leaf"] and not o["disabled"] and school_other_option(o["text"])]
        if len(others) == 1:
            choice = others[0]
    if not choice:
        # Browse from the top: the value can be a leaf under a category ("Job Board" > "LinkedIn").
        await inp.fill("")
        await inp.press("Enter")
        opts = await wait_options(
            frame,
            None,
            timeout=2,
            selector="[data-automation-id=promptOption],[role=option]",
            owner=container,
        )
        seen[""] = [o["text"] for o in opts][:8]
        # Some taxonomies are a flat catalog. Exact search may return nothing
        # even though an evidence-supported broader category is offered here.
        choice = await supported_leaf(opts)
        categories = [o["text"] for o in opts if not o["leaf"]][:12] if not school_scope else []
        # Likely homes for an online source first ("Social Media" > "LinkedIn").
        likely = re.compile(r"social|job ?board|online|internet|web|network|media", re.I)
        categories.sort(key=lambda text: not likely.search(text))
        for index, name in enumerate(categories):
            if choice:
                break
            if index:
                # Reopen from the top instead of depending on a Back control: tenants
                # without one used to stop after the first category (Viridien).
                await frame.page.keyboard.press("Escape")
                inp = await open_prompt(container)
                await inp.fill("")
                await inp.press("Enter")
                opts = await wait_options(
                    frame,
                    None,
                    timeout=2,
                    selector="[data-automation-id=promptOption],[role=option]",
                    owner=container,
                )
            category = next((o for o in opts if o["text"] == name and not o["leaf"]), None)
            if not category:
                continue
            await click_option(frame, category, owner=container)
            inner = await wait_options(
                frame,
                None,
                timeout=2,
                selector="[data-automation-id=promptOption],[role=option]",
                owner=container,
            )
            choice = await supported_leaf(inner)
            if choice:
                break
    if not choice:
        await inp.press("Escape")
        error = ValueError("No matching option is offered")
        error.offered = seen
        raise error
    await click_option(frame, choice, owner=container)
    if not choice["leaf"]:
        inner = await wait_options(
            frame,
            None,
            timeout=2,
            selector="[data-automation-id=promptOption],[role=option]",
            owner=container,
        )
        leaf = best_option([o for o in inner if o["leaf"]], value)
        if not leaf:
            raise ValueError("The selected category needs a specific choice")
        await click_option(frame, leaf, owner=container)
        choice = leaf
    # Opening a category is not a committed field value. Read the selected
    # item from this widget before closing its menu or recording an answer.
    deadline = time.monotonic() + 2
    committed = None
    while time.monotonic() < deadline:
        selected = await container.evaluate("""e => {
          const root=e.closest('[data-automation-id^="formField-"],[data-automation-id="multiSelectContainer"]') || e.parentElement;
          return [...(root?.querySelectorAll('[data-automation-id="selectedItem"]') || [])]
            .map(n=>n.innerText.trim()).filter(Boolean);
        }""")
        committed = best_option(selected, choice["text"])
        if committed:
            break
        await asyncio.sleep(0.1)
    try:
        await frame.page.keyboard.press("Escape")
    except Exception:
        pass
    if not committed:
        raise ValueError("The selected prompt option did not commit to this field")
    return committed


async def date_parts(frame, field, value):
    text = date_text(field, value)
    values = dict(zip([p["kind"] for p in field["parts"]], text.split("/"), strict=True))
    for part in field["parts"]:
        loc = frame.locator(f'[data-jp-id="{part["dom_id"]}"]')
        display = loc.locator("..").locator('[data-automation-id$="-display"]')
        if await display.count() == 1 and await display.is_visible():
            # The visible segment changes Workday's active-part state. Focusing
            # the covered input directly can send day/year edits to the month.
            await display.click(timeout=4000)
        else:
            await loc.focus(timeout=4000)
        # Workday selects a date segment again after each key, so typing "12"
        # can leave only "2". Commit the complete segment through its input event.
        await loc.fill(values[part["kind"]], timeout=4000)
        await loc.press("Tab")
    await asyncio.sleep(0.1)
    return text


async def pills(el, value):
    buttons = el.locator("button[aria-pressed],[role=button][aria-pressed]")
    texts = [x.strip() for x in await buttons.all_inner_texts()]
    choice = best_option(texts, value)
    if choice is None:
        raise ValueError("No matching choice is offered")
    target = buttons.nth(texts.index(choice))
    if await target.get_attribute("aria-pressed") != "true":
        await field_click(target)
    return choice


async def multi_pills(frame, field, value):
    """Commit the whole supported choice set through this observed question's members."""
    members, labels = field.get("member_ids", []), field.get("options", [])
    wanted = {norm(v) for v in str(value).splitlines() if v.strip()}
    offered = [norm(v) for v in labels]
    if not members or len(members) != len(labels) or len(set(offered)) != len(offered):
        raise ValueError("Multiple choice ownership changed; observe again")
    if not wanted or not wanted.issubset(offered):
        raise ValueError("No matching choice is offered")
    selected = (
        "e=>e.getAttribute('aria-pressed')==='true'||e.classList.contains('cx-select-pill-section--selected')"
    )
    controls = []
    for member, label in zip(members, labels, strict=True):
        control = frame.locator(f'[data-jp-id="{member}"]')
        if await control.count() != 1 or not await control.is_visible():
            raise ValueError("Multiple choice member is no longer available")
        state = bool(await control.evaluate(selected))
        want = norm(label) in wanted
        if state != want and await control.get_attribute("aria-disabled") == "true":
            raise ValueError("Multiple choice member is disabled")
        controls.append((control, label, want))
    for control, _label, want in controls:
        if bool(await control.evaluate(selected)) != want:
            await field_click(control, timeout=2000)
            deadline = time.monotonic() + 2
            while bool(await control.evaluate(selected)) != want:
                if time.monotonic() >= deadline:
                    raise ValueError("The website did not commit the observed choice")
                await asyncio.sleep(0.05)
    actual = [label for control, label, _ in controls if await control.evaluate(selected)]
    if {norm(label) for label in actual} != wanted:
        raise ValueError("The website did not commit the observed choice")
    return "\n".join(actual)


async def select(el, value, widget=""):
    options = await el.evaluate(
        "e => [...e.options].filter(o => !o.disabled && !o.closest('optgroup[disabled]') && o.value !== '').map(o => o.text.trim())"
    )
    choice = best_option(options, value)
    if choice is None:
        raise ValueError("No matching option is offered")
    if widget == "proxy":
        # select2/chosen hide the native select; set it and notify the plugin.
        await el.evaluate(
            """(e, label) => {
              const o = [...e.options].find(x => x.text.trim() === label);
              e.value = o.value; o.selected = true;
              e.dispatchEvent(new Event('input', {bubbles: true}));
              e.dispatchEvent(new Event('change', {bubbles: true}));
              if (window.jQuery) { try { window.jQuery(e).trigger('change').trigger('chosen:updated'); } catch (_) {} }
            }""",
            choice,
        )
    else:
        # Text may include whitespace or differ from the native option label.
        # Choose its observed index, and refuse ambiguous normalized labels.
        indices = await el.evaluate(
            """(e,text)=>[...e.options].flatMap((o,index)=>
            !o.disabled && !o.closest('optgroup[disabled]') && o.value!=='' && o.text.trim()===text ? [index]:[])""",
            choice,
        )
        if not indices:
            raise ValueError("Native option is ambiguous or changed")
        # Options with the same visible text are one choice to a person; ZS (iCIMS 2026-10-26) lists "Job Board"
        # twice, and refusing it left a required source question unanswered on every pass.
        await el.select_option(index=indices[0], timeout=4000)
    # Leave the control as a person would. SuccessFactors clears an earlier
    # "…is required"/aria-invalid only on blur, so readback saw a set value as invalid.
    try:
        await el.evaluate("e => { e.focus(); e.blur(); }")
    except Exception:
        pass
    return choice


async def checkbox(el, value, widget=""):
    want = checkbox_value(value) == "true"
    is_input = await el.evaluate("e => e.tagName === 'INPUT'")
    if is_input:
        try:
            await el.set_checked(want, timeout=3000, force=widget == "label")
        except Exception:
            if await el.is_checked() != want:
                await el.evaluate("e => (e.labels && e.labels[0] ? e.labels[0] : e).click()")
    elif (await el.get_attribute("aria-checked") == "true") != want:
        await el.click(timeout=4000)
    return "true" if want else "false"
