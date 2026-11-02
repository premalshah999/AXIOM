"""Reading every choice a picker offers, for the owner who is about to be asked to pick one.

Owner 2026-11-01: "it gives incomplete questions or answers dropdown selections". 8 of the 696 questions on file were pickers
(combobox, dropdown, prompt) that reached the owner with no choices at all ("Month", "Country", "Restricted Party Screening ...");
13 more held a window of a longer list ("State/Province" listing four of its provinces, a "Wellmark founded" list of 2032, October,
Independent, 1939). The scan reads a menu once, 1.2 to 2 seconds after opening it, and keeps the first stable read: a menu that
fetches its rows when opened, shows a few rows at a time (a virtualised list) or opens empty until something is typed is read
as empty or as its first screen. This module is the patient read, used when a question is about to leave the page for the owner:

- open the menu, wait for its rows for several seconds (a menu that loads from a server);
- scroll the menu's own list to its end, collecting every row (a virtualised list shows a few at a time);
- a menu that stays empty with nothing in it but a text box is a search: it is marked `search_only` so the owner is told to type
  the start of the answer, never shown an empty dropdown.
"""

import asyncio
import time

from playwright.async_api import Error as PlaywrightError

from . import widgets

# The scrollable list that holds the visible options, scrolled one screen: False when it is already at its end or does not scroll.
SCROLL_JS = r"""({scope, selector}) => {
  const root = scope ? (document.getElementById(scope) || document) : document;
  const shown = [...root.querySelectorAll(selector)].filter(o => o.getClientRects().length);
  if (!shown.length) return false;
  let list = shown[0].parentElement;
  while (list && list !== document.body && !(list.scrollHeight > list.clientHeight + 4 && /auto|scroll/.test(getComputedStyle(list).overflowY))) list = list.parentElement;
  if (!list || list === document.body) return false;
  const before = list.scrollTop;
  list.scrollTop = before + Math.max(40, list.clientHeight * 0.9);
  return list.scrollTop !== before;
}"""


def usable(option):
    text = " ".join(str(option.get("text", "")).split())
    return bool(text) and not option.get("disabled") and not widgets.placeholder_option(text)


async def read_all(element, frame, *, wait=5.0, limit=400, rounds=150, budget=9.0):
    """(options, complete, searchable): every row the open menu offers, whether the end of the list was reached, and whether the
    control is a text box that lists nothing until something is typed. Leaves the menu for the caller to close."""
    await widgets.open_combobox(element)
    scope = await widgets._popup_scope(element)
    first = await widgets.wait_options(frame, scope, timeout=wait, owner=element)
    seen = {}
    for option in first:
        if usable(option):
            seen.setdefault(" ".join(option["text"].split()), None)
    editable = await element.evaluate("e => ['INPUT','TEXTAREA'].includes(e.tagName) && !e.readOnly && !e.disabled")
    if not seen:
        return [], False, bool(editable)
    complete = True
    stop = time.monotonic() + budget
    for _ in range(rounds):
        if len(seen) >= limit or time.monotonic() >= stop:
            complete = False
            break
        try:
            moved = await frame.evaluate(SCROLL_JS, {"scope": scope, "selector": widgets.OPTION_SELECTOR})
        except PlaywrightError:
            break
        # At the end of what is loaded a menu may still be fetching its next page: give it a moment before calling that the end.
        await asyncio.sleep(0.2 if moved else 0.6)
        before = len(seen)
        for option in await widgets.visible_options(frame, scope):
            if usable(option):
                seen.setdefault(" ".join(option["text"].split()), None)
        if not moved and len(seen) == before:
            break
    return list(seen)[:limit], complete, False


async def complete_choices(form, deadline_seconds=40.0):
    try:
        await _complete_choices(form, deadline_seconds)
    except Exception:  # Reading more choices must never be what stops an application from reaching the owner.
        pass


async def _complete_choices(form, deadline_seconds):
    """Fill in the choices of the form's pending pickers (forms.Form.pending) that reached the owner without all of them.

    Every picker that is about to be asked is read again, because a list that shows 15 rows at a time looks whole at 15. Anything this cannot read stays as it was: the owner is still asked, with
    the wording and the options the scan had."""
    stop = time.monotonic() + deadline_seconds
    read = 0
    for item in form.pending[:12]:
        context = item.setdefault("context", {})
        if item.get("type") not in {"combobox", "dropdown", "prompt"} or context.get("choices_read"):
            continue
        listed = [o for o in item.get("options") or [] if not widgets.placeholder_option(str(o))]
        # A list read whole is read again too: a menu that shows 15 rows at a time (a virtualised list) looks complete at 15.
        if time.monotonic() >= stop or read >= 6 or item.get("field_id") not in form.frames:
            continue
        context["choices_read"] = True
        read += 1
        field = form.fields.get(item["field_id"])
        if not field:
            continue
        try:
            element = form.locator(item["field_id"])
            frame = form.frames[item["field_id"]]
            await widgets.dismiss_pickers(frame)
            async with asyncio.timeout(min(18.0, max(2.0, stop - time.monotonic()))):
                options, complete, searchable = await read_all(element, frame)
        except (TimeoutError, PlaywrightError, ValueError, KeyError):
            continue
        finally:
            try:
                await widgets.close_picker(form.locator(item["field_id"]), form.frames.get(item["field_id"]))
            except Exception:
                pass
        if len(options) >= len(listed):
            item["options"] = options
            field["options"] = options
        if options:
            # A list that fetches matches as you type ends where its first page ends: that end is not the catalog's.
            try:
                searches = await element.evaluate("e => ['list','both'].includes(e.getAttribute('aria-autocomplete'))")
            except PlaywrightError:
                searches = False
            context["options_partial"] = not complete or (searches and len(options) >= 10)
        elif searchable:
            context["search_only"] = True
            context["options_partial"] = True
        form.emit("option_observation", "Read the choices for the owner's question",
                  {"field": item["question"], "options": len(options), "complete": complete, "search_only": searchable})
