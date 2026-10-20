"""Bounded selection for observed Oracle/Dayforce widget signatures.

These drivers never infer answers. They commit a supplied, supported value to an
explicitly owned popup and verify the provider's displayed selection.
"""

import asyncio
import re
import time
from calendar import month_name
from urllib.parse import urlsplit

from playwright.async_api import Error as PlaywrightError

from . import widgets

OBSERVED_HOSTS = {"jobs.dayforcehcm.com": "dayforce"}


def combo_host_family(url):
    try:
        location = urlsplit(url)
        family = OBSERVED_HOSTS.get(location.hostname) if location.scheme == "https" else None
        if location.username or location.password or location.port not in {None, 443}:
            return None
        # The observed CX control is shared across Oracle employers/regions.
        # A single canary hostname here silently disabled the verified driver
        # for every other tenant. Bind it to Oracle's candidate route and DOM
        # signature, not to one company's installation.
        if (
            location.scheme == "https"
            and (location.hostname or "").endswith(".oraclecloud.com")
            and location.path.startswith("/hcmUI/CandidateExperience/")
        ):
            family = "oracle"
    except ValueError:
        return None
    return family


async def combo_family(frame, el):
    family = combo_host_family(frame.url)
    if family is None:
        # Unsupported portals cannot use this driver. In particular, do not
        # wait on an iCIMS locator from a document replaced during profile save.
        return None
    try:
        signature = await el.evaluate(
            """e=>e.matches('input.cx-select-input[role=combobox]')?'oracle':
            e.matches('input.ant-select-selection-search-input[role=combobox]') && e.closest('.ant-select')?'dayforce':null""",
            timeout=500,
        )
    except PlaywrightError:
        # A control the page replaced (Honeywell Oracle, 2026-10-14) takes the generic path
        # instead of failing the run at this check.
        return None
    return family if signature == family else None



async def read_combos(frame, dom_ids):
    """Read every observed provider combo in one renderer call, without mutating it."""
    family = combo_host_family(frame.url)
    if not family or not dom_ids:
        return {}
    try:
        async with asyncio.timeout(1):
            return await frame.evaluate("""({family, ids}) => {
                const wanted=new Set(ids), result={};
                const roots=[document];
                for(const root of roots) {
                  for(const host of root.querySelectorAll('*'))if(host.shadowRoot)roots.push(host.shadowRoot);
                  for(const e of root.querySelectorAll('input[role=combobox][data-jp-id]')) {
                    const id=e.dataset.jpId;
                    if(!wanted.has(id))continue;
                    if(family==='oracle' && e.matches('input.cx-select-input'))
                        result[id]={value:e.value,family};
                    if(family==='dayforce' && e.matches('input.ant-select-selection-search-input') && e.closest('.ant-select')) {
                        const values=e.closest('.ant-select').querySelectorAll('.ant-select-selection-item');
                        result[id]={value:values.length===1?(values[0].getAttribute('title')||values[0].innerText).trim():'',family};
                    }
                  }
                }
                return result;
            }""", {"family":family,"ids":dom_ids})
    except (TimeoutError, PlaywrightError):
        return {}  # The fresh scanner's value remains authoritative on replacement.

def component_value(label, value):
    if month_component(label) and re.fullmatch(r"0?[1-9]|1[0-2]", str(value)):
        return month_name[int(value)]
    return value


def month_component(label):
    return bool(
        re.fullmatch(
            r"(?:(?:start|end|graduation|completion|birth)\s+(?:date\s+)?)?month",
            label.strip(),
            re.I,
        )
    )


def pick_qualified(rows, wanted, state):
    """Pick the one offered row that qualifies a ZIP or city; anything ambiguous returns None.

    CX remote address rows carry qualifiers, e.g. "Ann Arbor, Washtenaw, Michigan,
    United States" for a city and "48104, Ann Arbor, Michigan, United States" for a
    ZIP, so exact equality with the bare value cannot match them. Split on the raw
    commas first: widgets.norm() strips punctuation, so it must run per segment.
    """
    wanted = str(wanted or "").strip()
    zip_match = re.fullmatch(r"(\d{5})(?:-\d{4})?", wanted)
    if zip_match:
        pattern = re.compile(rf"{zip_match[1]}(?!\d)")
        hits = [o for o in rows if pattern.match(str(o["text"]).strip())]
    else:
        city = widgets.norm(wanted)
        if not city or not widgets.norm(state):
            return None
        states = widgets._alias_group(state)
        hits = []
        for o in rows:
            parts = [widgets.norm(part) for part in str(o["text"]).split(",")]
            if len(parts) >= 2 and parts[0] == city and any(part in states for part in parts[1:]):
                hits.append(o)
    return hits[0] if len(hits) == 1 else None


async def _options(el, family):
    return await el.evaluate(
        r"""(e,family)=>{
      const refs=(e.getAttribute('aria-controls')||e.getAttribute('aria-owns')||'').split(/\s+/).filter(Boolean);
      if(refs.length!==1)return [];
      const owned=document.getElementById(refs[0]);if(!owned)return [];
      const root=family==='dayforce'?owned.closest('.ant-select-dropdown'):owned;
      if(!root)return [];
      const selector=family==='dayforce'?'.ant-select-item-option':
        '[role=option],[role=gridcell],[role=row]';
      // Grid rows wrap cells: keep only the innermost matches so one visible row is one option.
      const found=[...root.querySelectorAll(selector)];
      const leaves=found.filter(o=>!found.some(p=>p!==o&&o.contains(p)));
      return leaves.filter(o=>o.getClientRects().length &&
        getComputedStyle(o).visibility!=='hidden' && o.getAttribute('aria-disabled')!=='true' &&
        !o.classList.contains('ant-select-item-option-disabled')).slice(0,1000).map(o=>{
          o.dataset.jpAtsOption=o.dataset.jpAtsOption||('a'+Math.random().toString(36).slice(2));
          const content=o.querySelector('.ant-select-item-option-content')||o;
          return {id:o.dataset.jpAtsOption,text:(content.innerText||'').replace(/\s+/g,' ').trim(),
            selected:o.getAttribute('aria-selected')==='true'};
        }).filter(o=>o.text);
    }""",
        family,
        timeout=500,
    )


async def _wait_owned_options(el, family, timeout, *, previous=None):
    previous_signature = tuple((o["id"], o["text"]) for o in previous) if previous is not None else None
    try:
        async with asyncio.timeout(timeout):
            while True:
                options = await _options(el, family)
                signature = tuple((o["id"], o["text"]) for o in options)
                if options and signature != previous_signature:
                    return options
                await asyncio.sleep(0.05)
    except (TimeoutError, PlaywrightError):
        return []


async def select_combo(frame, el, field, value, choose=None, *, rematch=None, applicant_state=""):
    family = await combo_family(frame, el)
    if not family:
        raise ValueError("ATS combobox signature/origin is not observed")

    async def refreshed_owner():
        nonlocal el
        current = await rematch()
        if await combo_family(frame, current) != family:
            raise ValueError("ATS selection owner changed its control or origin")
        el = current
        return current

    refresh = refreshed_owner if rematch else None
    if family == "dayforce":
        trigger = el.locator(
            "xpath=ancestor::*[contains(concat(' ',normalize-space(@class),' '),' ant-select-selector ')][1]"
        )
        await trigger.click(timeout=4000)
    else:
        await widgets.field_click(el)
    wanted = component_value(field["label"], value) if family == "oracle" else value
    autosuggest = family == "oracle" and await el.evaluate(
        "e=>e.classList.contains('cx-select-input--auto-suggest')", timeout=500
    )
    school = bool(re.search(r"\bschool\b|\buniversity\b|\binstitution\b|\bcollege\b", field["label"], re.I))
    # Goldman Sachs (Oracle) 2026-10-18: "Name of Latest Employer" lists 250+ firms; the applicant's employer is not
    # one, and only the generic combobox knew to commit the list's own "Other" (e4ffe71). Four runs stalled here.
    employer = not school and bool(re.search(
        r"\b(?:latest|current|most recent|previous|former|last|present) employer\b|\bname of (?:your )?"
        r"(?:latest |current |most recent |previous |former )?employer\b|^\s*employer(?: name)?\s*\*?$",
        field["label"], re.I,
    ))

    def eligible(rows):
        return [o for o in rows if not school or widgets.school_option_matches(value, o["text"])]

    options = [] if autosuggest else await _wait_owned_options(el, family, 2)
    choice = widgets.best_option(eligible(options), wanted)
    if not choice and family == "oracle":
        choice = pick_qualified(eligible(options), wanted, applicant_state)
    # CX remote address controls do not necessarily offer anything on click.
    # Use the supported answer as a query, then select only this input's results.
    # Autosuggest is a distinct vendor capability: it accepts typed free text,
    # while ordinary CX selects require an offered option.
    if not choice and family == "oracle" and await el.evaluate("e=>!e.readOnly && !e.disabled", timeout=500):
        previous = options
        await el.fill("", timeout=1000)
        await el.press_sequentially(str(wanted), delay=12, timeout=4000)
        # A remote search may leave its old catalog visible during debounce.
        # New owned node/content identities establish a response; input text
        # alone does not establish that those rows answer the supplied query.
        options = await _wait_owned_options(el, family, 1.25 if autosuggest else 3, previous=previous)
        if autosuggest and not options:
            return await _wait_committed(el, family, wanted, field, refresh=refresh)
        choice = widgets.best_option(eligible(options), wanted)
        if not choice and family == "oracle":
            choice = pick_qualified(eligible(options), wanted, applicant_state)
        if not choice and not autosuggest and not school:
            # Remote catalogs often index "United States", while the saved
            # fact says "United States of America". Search bounded supported
            # equivalents, then commit an actual owned option.
            aliases = sorted(widgets._alias_group(wanted) - {widgets.norm(wanted)}, key=len, reverse=True)[:3]
            for query in aliases:
                previous = options
                await el.fill("", timeout=1000)
                await el.press_sequentially(query, delay=12, timeout=4000)
                options = await _wait_owned_options(el, family, 2, previous=previous)
                choice = widgets.best_option(eligible(options), wanted)
                if choice:
                    break
    if not choice and school:
        others = [o for o in options if widgets.school_other_option(o["text"])]
        if not others and family == "oracle" and await el.is_editable():
            previous = options
            await el.fill("")
            await el.press_sequentially("Other", delay=12, timeout=4000)
            options = await _wait_owned_options(el, family, 2, previous=previous)
            others = [o for o in options if widgets.school_other_option(o["text"])]
        if len(others) == 1:
            choice = others[0]
    if not choice and employer:
        others = [o for o in options if widgets.employer_other_option(o["text"])]
        if not others and family == "oracle" and await el.is_editable():
            previous = options
            await el.fill("")
            await el.press_sequentially("Other", delay=12, timeout=4000)
            options = await _wait_owned_options(el, family, 2, previous=previous)
            others = [o for o in options if widgets.employer_other_option(o["text"])]
        if len(others) == 1:
            choice = others[0]
    if choice and sum(widgets.norm(o["text"]) == widgets.norm(choice["text"]) for o in options) != 1:
        choice = None
    if not choice and choose and eligible(options):
        selected = await choose([o["text"] for o in eligible(options)])
        matches = [o for o in eligible(options) if o["text"] == selected]
        choice = matches[0] if len(matches) == 1 else None
    if not choice:
        offered = [o["text"][:60] for o in eligible(options)[:5]]
        await el.press("Escape")
        detail = f"; first offered: {offered}" if offered else "; no options were readable"
        raise ValueError("No unique supported option in this ATS field's owned popup" + detail)
    # Reobserve after reasoning: a stale choice must not target a sibling popup.
    fresh = await _options(el, family)
    if not any(o == choice for o in fresh):
        raise ValueError("ATS option became stale before selection")
    await frame.locator(f'[data-jp-ats-option="{choice["id"]}"]').click(timeout=4000)
    if autosuggest:
        # CX autosuggest may render a qualified address row yet retain exactly
        # the supported street query as its accepted free-text value. Verify
        # that supplied fact directly. Reopening by typing would change it.
        return await _wait_committed(el, family, (wanted, choice["text"]), field, refresh=refresh)
    # CX address rows include city/county/state qualifiers while the committed
    # input can display only the supplied city/ZIP. A short display still has
    # to prove the exact selected row in the owned popup below.
    expected = (wanted, choice["text"]) if family == "oracle" else choice["text"]
    actual = await _wait_committed(el, family, expected, field, refresh=refresh)
    if family == "oracle" and widgets.norm(actual) != widgets.norm(choice["text"]):
        # Several countries share the same displayed calling code. A valid +1
        # alone cannot prove that a USA option replaced a Canada selection.
        # Reopen only this owner and observe its selected option identity.
        try:
            async with asyncio.timeout(5):
                await widgets.field_click(el, timeout=2000)
                selected = [o for o in await _wait_owned_options(el, family, 2) if o["selected"]]
                if await el.count() != 1:
                    raise ValueError("ATS short display did not verify the selected owned option")
                await widgets.close_picker(el, frame)
                if len(selected) != 1 or selected[0]["text"] != choice["text"]:
                    raise ValueError("ATS short display did not verify the selected owned option")
                actual = await _wait_committed(el, family, expected, field, refresh=refresh)
        except (TimeoutError, PlaywrightError):
            raise ValueError("ATS short display did not verify the selected owned option") from None
    return actual


async def _wait_committed(el, family, expected, field, *, refresh=None):
    """Bound all readback operations, including a disappeared/remounted input."""
    expected_values = expected if isinstance(expected, tuple) else (expected,)
    try:
        async with asyncio.timeout(2):
            if await el.count() != 1 and refresh:
                el = await refresh()
            await el.blur(timeout=500)
            stable_since = None
            while True:
                if await el.count() != 1 and refresh:
                    el = await refresh()
                # Value, expanded state and validity belong to one render. Four
                # serial CDP calls per poll used most of this two-second budget
                # on background tabs and could mix states across a remount.
                state = await el.evaluate(
                    """(e,family)=>{
                      const values=family==='dayforce'
                        ? e.closest('.ant-select')?.querySelectorAll('.ant-select-selection-item'):null;
                      const value=family==='dayforce'
                        ? (values?.length===1?(values[0].getAttribute('title')||values[0].innerText).trim():'')
                        : (e.value||'');
                      return {value,closed:e.getAttribute('aria-expanded')==='false',
                        valid:e.getAttribute('aria-invalid')!=='true' && (!e.validity || e.validity.valid)};
                    }""", family, timeout=500,
                )
                actual = state["value"]
                accepted = state["closed"] and state["valid"] and any(
                    widgets.same_value({**field, "provider_combo": family}, actual, candidate)
                    for candidate in expected_values
                )
                if accepted:
                    stable_since = stable_since or time.monotonic()
                    # CX updates display, observable value and blur validation
                    # on separate renders. Stability is an extra guard; the
                    # final form readback still verifies every expected value.
                    if time.monotonic() - stable_since >= 0.2:
                        return actual
                else:
                    stable_since = None
                await asyncio.sleep(0.05)
    except (TimeoutError, PlaywrightError):
        raise ValueError("ATS selection did not commit to its owning field") from None


async def read_combo(frame, el):
    family = await combo_family(frame, el)
    try:
        if family == "dayforce":
            return await el.evaluate(
                """e=>{
              const values=e.closest('.ant-select')?.querySelectorAll('.ant-select-selection-item');
              return values?.length===1?(values[0].getAttribute('title')||values[0].innerText).trim():'';
            }""",
                timeout=3000,
            )
        if family == "oracle":
            return await el.input_value(timeout=3000)
    except PlaywrightError:
        # A re-rendered CX control (JPMorgan Oracle, 2026-10-14) is re-read by the generic path
        # instead of failing the run after Playwright's 30 s default wait.
        return None
    return None
