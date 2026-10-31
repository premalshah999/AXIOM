from unittest.mock import AsyncMock

import pytest
from jobpilot import widgets


async def test_no_search_result_browses_flat_catalog_and_commits_model_leaf(page):
    await page.set_content("""<div data-automation-id=multiSelectContainer>
      <div data-automation-id=multiselectInputContainer id=study>
        <input onclick="catalog()" onkeydown="if(event.key==='Enter'){catalog();event.preventDefault()}">
      </div></div><div id=menu role=listbox hidden></div>
      <script>
      function catalog(){let m=document.getElementById('menu');m.hidden=false;
        m.innerHTML=document.querySelector('input').value
          ? '<div role=option>No items</div>'
          : '<div role=option onclick="pick(this)">Computer and Information Science</div><div role=option onclick="pick(this)">Accounting</div>';
      }
      function pick(e){document.getElementById('study').insertAdjacentHTML('beforeend',
        '<span data-automation-id=selectedItem>'+e.textContent+'</span>');e.parentElement.hidden=true;}
      </script>""")
    choose = AsyncMock(return_value="Computer and Information Science")
    assert (
        await widgets.prompt(page.main_frame, page.locator("#study"), "Information Technology", choose)
        == "Computer and Information Science"
    )
    choose.assert_awaited_once_with(["Computer and Information Science", "Accounting"])


async def test_model_cannot_commit_unoffered_catalog_value(page):
    await page.set_content("""<div data-automation-id=multiSelectContainer>
      <div data-automation-id=multiselectInputContainer id=study><input onclick="document.getElementById('menu').hidden=false"></div>
      </div><div id=menu role=listbox hidden><div role=option>Accounting</div></div>""")
    choose = AsyncMock(return_value="Invented option")
    with pytest.raises(ValueError, match="No matching option"):
        await widgets.prompt(page.main_frame, page.locator("#study"), "Information Technology", choose)
    assert choose.await_count <= 2
    assert await page.locator("[data-automation-id=selectedItem]").count() == 0


def test_school_prompt_fallback_queries_keep_the_institution():
    from jobpilot.widgets import prompt_fallback_queries, school_option_matches

    queries = prompt_fallback_queries("University of Michigan, Ann Arbor", school_scope=True)
    assert queries == ["University of Michigan", "Michigan", "Other"]
    # Shorter searches never widen what may be selected.
    assert school_option_matches(
        "University of Michigan, Ann Arbor", "University of Michigan - Ann Arbor"
    )
    assert not school_option_matches(
        "University of Michigan, Ann Arbor", "University of Michigan-Dearborn"
    )
    assert prompt_fallback_queries("Information Systems") == ["Information"]


def test_an_abbreviated_school_option_is_searched_by_its_distinctive_word_and_matches_the_profile():
    from jobpilot.widgets import prompt_fallback_queries, school_option_matches

    # Jefferson Health's Workday abbreviates "Univ of <State> <Campus>"; a resumed draft searched that, then "Univ",
    # which lists the first 100 schools alphabetically, and never committed the field (2026-10-23).
    assert prompt_fallback_queries("Univ of Michigan Ann Arbor", school_scope=True) == ["Michigan", "Other"]
    assert school_option_matches("University of Michigan, Ann Arbor", "Univ of Michigan Ann Arbor")
    assert school_option_matches("Univ of Michigan Ann Arbor", "Univ of Michigan Ann Arbor")
    assert not school_option_matches("University of Michigan, Ann Arbor", "Univ of Michigan Dearborn")


def test_numeric_salary_matches_only_the_exact_currency_option():
    from jobpilot.widgets import best_option

    options = ["$80,000", "$85,000", "$90,000", "$300,000 or higher", "Prefer not to say"]
    assert best_option(options, "85000") == "$85,000"
    assert best_option(options, "85K") == "$85,000"
    assert best_option(options, "87000") is None
    ranges = ["$60,000 - $80,000", "$80,000 - $100,000", "$100,000 - $120,000", "$300,000+"]
    assert best_option(ranges, "85000") == "$80,000 - $100,000"
    assert best_option(ranges, "80000") is None  # shared boundary: ambiguous
    assert best_option(ranges, "350000") == "$300,000+"


async def test_react_select_multi_commits_a_chip(page):
    # Greenhouse "mark all that apply" gender question (DoorDash).
    await page.set_content("""<div class="select__control">
      <div class="select__value-container select__value-container--is-multi" id=chips>
        <input role=combobox aria-expanded=false aria-controls=menu class=select__input
          onclick="open_()" onkeydown="open_()"></div></div>
      <div id=menu role=listbox hidden></div>
      <script>
      const opts=['Male','Female','Non-binary',"I don't wish to answer"];
      function open_(){const m=document.getElementById('menu');m.hidden=false;
        document.querySelector('input').setAttribute('aria-expanded','true');
        m.innerHTML=opts.map(o=>'<div role=option onclick="pick(this)">'+o+'</div>').join('');}
      function pick(o){document.getElementById('chips').insertAdjacentHTML('afterbegin',
        '<div class=select__multi-value><div class=select__multi-value__label>'+o.textContent+'</div></div>');}
      document.querySelector('input').addEventListener('keydown',e=>{if(e.key==='Escape'){
        document.getElementById('menu').hidden=true;e.target.setAttribute('aria-expanded','false');}});
      </script>""")
    got = await widgets.combobox(page.main_frame, page.locator("input"), "I don't wish to answer")
    assert "I don't wish to answer" in got


async def test_phone_widget_that_ignores_value_writes_gets_typed_digits(page):
    # Greenhouse intl-tel-input: a programmatic value write is discarded on blur.
    await page.set_content("""<div class=field-wrapper><input id=phone type=tel>
      <span id=err></span></div>
      <script>let typed=false;const p=document.getElementById('phone');
      p.addEventListener('keydown',()=>typed=true);
      p.addEventListener('blur',()=>{if(!typed){p.value='';document.getElementById('err').textContent='Phone number is too short';}
        else document.getElementById('err').textContent='';});</script>""")
    el = page.locator("#phone")
    await el.fill("555-555-0100")
    assert await widgets.phone_rejected(el, "555-555-0100")
    await el.fill("")
    await el.press_sequentially("5555550100", delay=5)
    assert not await widgets.phone_rejected(el, "555-555-0100")


async def test_browse_button_opens_a_chooser_that_receives_the_resume(page, tmp_path):
    resume = tmp_path / "resume.pdf"
    resume.write_bytes(b"%PDF-1.4 fixture")
    await page.set_content("""<p>CV/Resume: upload your resume</p><button id=b onclick="document.getElementById('f').click()">Browse</button>
      <input id=f type=file hidden onchange="document.title=this.files[0].name">""")
    assert widgets.opens_resume_chooser("Browse", await page.inner_text("body"), uploaded=False)
    assert not widgets.opens_resume_chooser("Browse", "Upload a cover letter", uploaded=False)
    assert not widgets.opens_resume_chooser("Browse", "Resume", uploaded=True)
    assert not widgets.opens_resume_chooser("Browse cover letter", "Resume", uploaded=False)
    for label in ("Select files", "Select file", "Choose File", "Upload a file", "Upload", "Attach resume"):
        assert widgets.opens_resume_chooser(label, "Upload your resume (5MB max)", uploaded=False), label
    assert not widgets.opens_resume_chooser("Select", "Resume", uploaded=False)
    attached = await widgets.click_for_file_chooser(page, lambda: page.locator("#b").click(), resume)
    assert attached and await page.title() == "resume.pdf"
    await page.set_content("<button id=n>Browse</button>")
    assert (
        await widgets.click_for_file_chooser(page, lambda: page.locator("#n").click(), resume, timeout=600)
        is False
    )


async def test_reupload_overwrite_dialog_is_cancelled(page):
    from jobpilot.forms import FormSession

    # UI5-style dialog: no dialog role, buttons in a footer beside the message.
    await page.set_content("""<section class="sapMDialog" id=dlg><header>Confirmation</header>
      <div class=content><span>Uploading your resume again will overwrite the prefilled data in your application. Do you wish to proceed?</span></div>
      <footer><button id=yes onclick="document.title='yes'">Yes</button>
      <button id=no onclick="document.getElementById('dlg').remove();document.title='cancelled'"><bdi>Cancel</bdi></button></footer></section>""")
    form = FormSession.__new__(FormSession)
    form.page, form.uploaded = page, False
    emitted = []
    form.emit = lambda *a, **k: emitted.append(a[0])

    async def settle(timeout=0):
        return None

    form.settle = settle
    assert await form.cancel_reupload_dialog() is True
    assert await page.title() == "cancelled" and form.uploaded and emitted == ["dialog_dismissed"]
    # The dialog only appears with a resume already attached; later wizard steps must not re-require it.
    assert form.upload_verified and form.resume_on_file
    assert await form.cancel_reupload_dialog() is False


async def test_clearing_one_tenant_keeps_other_tenants_signed_in(page):
    import re

    context = page.context
    await context.add_cookies(
        [
            {"name": "s", "value": "1", "url": "https://acme.wd5.myworkdayjobs.com/"},
            {"name": "s", "value": "2", "url": "https://other.wd5.myworkdayjobs.com/"},
        ]
    )
    host = "acme.wd5.myworkdayjobs.com"
    await context.clear_cookies(domain=re.compile(r"^\.?" + re.escape(host) + "$"))
    left = {c["domain"] for c in await context.cookies()}
    assert "other.wd5.myworkdayjobs.com" in left and host not in left


def test_k_suffixed_salary_brackets_are_read_with_the_suffix_on_both_numbers():
    from jobpilot.widgets import best_option, money_range

    assert money_range("26-30k") == (26000, 30000) and money_range("101-110k") == (101000, 110000)
    assert money_range("less than 25k") == (0, 25000) and money_range("171K+") == (171000, float("inf"))
    assert money_range("$80,000 - $100,000") == (80000, 100000)  # unchanged
    opts = ["less than 25k", "26-30k", "71-75k", "76-80k", "81-85k", "86-90k", "171K+"]
    assert best_option(opts, "80000") == "76-80k"
    assert best_option(opts, "85000") == "81-85k"
    assert best_option(opts, "200000") == "171K+"


async def test_phone_is_refilled_after_country_picker_clears_it(page):
    # Greenhouse order: the number is written, then the phone-country picker is committed
    # and clears the number. The widget discards programmatic writes unless keys were typed.
    from types import SimpleNamespace

    from jobpilot.forms import FormSession

    await page.set_content("""<select id=country onchange="typed=false;p.value=''">
      <option>United States +1</option><option>United Kingdom +44</option></select>
      <div class=field-wrapper><input id=phone data-jp-id=ph type=tel><span id=err></span></div>
      <script>let typed=false;const p=document.getElementById('phone');
      p.addEventListener('keydown',()=>typed=true);
      p.addEventListener('blur',()=>{if(!typed){p.value='';document.getElementById('err').textContent='Phone number is too short';}
        else document.getElementById('err').textContent='';});</script>""")
    field = {"id": "phone", "dom_id": "ph", "label": "Phone", "type": "tel", "valid": True, "required": True}
    form = FormSession.__new__(FormSession)
    form.page = page
    form.resolver = SimpleNamespace(profile=SimpleNamespace(autonomous=False, phone_country_code=""))
    form.fields, form.buttons = {"phone": field}, {}
    form.frames = {"phone": page.main_frame}
    form.expected, form.expected_slots, form.expected_labels = {}, {}, {}

    async def scan():
        return None  # The fixture's single field is already current.

    form.scan = scan
    form.expected["phone"] = await form.fill(field, "555-555-0100")
    # The widget rejects the programmatic write, so the form types the digits key by key.
    assert await page.input_value("#phone") == "5555550100"
    await page.select_option("#country", "United Kingdom +44")  # Commits after the number.
    assert await page.input_value("#phone") == ""
    await form.settle_phone()
    assert await page.input_value("#phone") != ""
    assert await widgets.phone_rejected(page.locator("#phone"), "555-555-0100") is False
    assert await page.input_value("#phone") == "5555550100"


UI5_STATE_PICKER = """<input id=state role=combobox aria-haspopup=listbox aria-controls=statelist
  aria-invalid=true class="sapMInputBaseInner" onfocus="openList()" onclick="openList()">
<ul id=statelist role=listbox hidden class=sapMSelectList></ul>
<script>
const states = ['Maine', 'Maryland', 'Massachusetts'];
const input = document.getElementById('state'), list = document.getElementById('statelist');
function openList() {
  list.hidden = false;
  input.setAttribute('aria-expanded', 'true');
  list.innerHTML = states.map(s => '<li role=option class=sapMSLI onmousedown="event.preventDefault()" onclick="commit(this)">' + s + '</li>').join('');
}
function commit(item) {
  input.value = item.textContent;
  list.hidden = true;
  input.setAttribute('aria-expanded', 'false');
}
input.addEventListener('blur', () => input.setAttribute('aria-invalid', 'false'));
</script>"""


async def test_ui5_state_picker_commits_clicked_full_name_and_clears_invalid(page):
    await page.set_content(UI5_STATE_PICKER)
    got = await widgets.combobox(page.main_frame, page.locator("#state"), "MD")
    assert got == "Maryland"
    assert await page.locator("#state").input_value() == "Maryland"
    assert await page.locator("#state").get_attribute("aria-invalid") != "true"


async def test_ui5_picker_whose_list_never_loads_raises_instead_of_hanging(page, monkeypatch):
    await page.set_content(
        '<input id=state role=combobox aria-haspopup=listbox aria-invalid=true class="sapMInputBaseInner">'
    )
    monkeypatch.setattr(widgets, "UI5_OPTION_TIMEOUT", 0.6)
    with pytest.raises(ValueError, match="No options became readable for this picker"):
        await widgets.combobox(page.main_frame, page.locator("#state"), "MD")


def _consent_dialog_html(*, remove=True, extra="", action="Submit"):
    onclick = " onclick=\"this.closest('[role=dialog]').remove()\"" if remove else ""
    return f"""<div role=dialog aria-modal=true><p>Please review before continuing</p>
      <label><input id=privacy type=checkbox checked> I have read and agree to the Privacy Policy</label>{extra}
      <button id=submit{onclick}>{action}</button></div>"""


async def _run_accept_terms(page, fields, controls, *, extra_fields=()):
    from types import SimpleNamespace

    from jobpilot.forms import FormSession

    calls, emitted = [], []
    top = {
        "title": "Please review before continuing",
        "text": "I have read and agree to the Privacy Policy",
        "field_ids": [f["id"] for f in fields],
        "control_ids": [b["id"] for b in controls],
    }

    async def scan():
        if not await page.locator("[role=dialog]").count():
            return {"dialogs": [], "fields": [], "controls": []}
        return {"dialogs": [top], "fields": fields, "controls": controls}

    async def click(control_id, auth_control=False):
        calls.append((control_id, auth_control))
        await page.locator(f"#{control_id}").click()

    async def cancel_reupload_dialog():
        return False

    async def fill(field, value):
        return None

    form = FormSession.__new__(FormSession)
    form.page = page
    form.resolver = SimpleNamespace(
        profile=SimpleNamespace(accept_all_application_terms=True),
        local=lambda f: SimpleNamespace(disposition="answer", value="true"),
    )
    form.scan, form.click, form.fill = scan, click, fill
    form.cancel_reupload_dialog = cancel_reupload_dialog
    form.emit = lambda kind, *a, **k: emitted.append(kind)
    await form.accept_terms_dialog()
    return calls, emitted


PRIVACY_BOX = {
    "id": "privacy",
    "type": "checkbox",
    "label": "I have read and agree to the Privacy Policy",
    "value": "true",
}


async def test_consent_only_privacy_dialog_submit_is_clicked_once(page):
    await page.set_content(_consent_dialog_html())
    calls, emitted = await _run_accept_terms(page, [PRIVACY_BOX], [{"id": "submit", "label": "Submit"}])
    assert calls == [("submit", True)]
    assert await page.locator("[role=dialog]").count() == 0
    assert emitted == ["consent"]


async def test_consent_dialog_that_survives_its_submit_stops_after_one_click(page):
    await page.set_content(_consent_dialog_html(remove=False))
    calls, emitted = await _run_accept_terms(page, [PRIVACY_BOX], [{"id": "submit", "label": "Submit"}])
    assert calls == [("submit", True)]
    assert emitted == ["consent", "dialog_stuck"]


async def test_privacy_dialog_with_text_input_is_not_auto_submitted(page):
    extra = "<label>Email <input id=email type=text></label>"
    await page.set_content(_consent_dialog_html(extra=extra))
    text_field = {"id": "email", "type": "text", "label": "Email", "value": ""}
    calls, emitted = await _run_accept_terms(
        page, [PRIVACY_BOX, text_field], [{"id": "submit", "label": "Submit"}]
    )
    assert calls == [] and emitted == []
    assert await page.locator("[role=dialog]").count() == 1


async def test_submit_application_dialog_with_file_input_is_not_auto_clicked(page):
    extra = "<input id=resume type=file>"
    await page.set_content(_consent_dialog_html(extra=extra, action="Submit application"))
    file_field = {"id": "resume", "type": "file", "label": "Resume", "value": ""}
    calls, _ = await _run_accept_terms(
        page, [PRIVACY_BOX, file_field], [{"id": "submit", "label": "Submit application"}]
    )
    assert calls == []
    # Without the file input, "Submit application" is still not a consent action.
    await page.set_content(_consent_dialog_html(action="Submit application"))
    calls, _ = await _run_accept_terms(page, [PRIVACY_BOX], [{"id": "submit", "label": "Submit application"}])
    assert calls == []
    assert await page.locator("[role=dialog]").count() == 1


def test_job_board_search_box_is_skipped_but_required_fields_are_not():
    from jobpilot.answers import job_catalog_search_box

    assert job_catalog_search_box(
        {"label": "Search for job title or keywords", "type": "text", "required": False}
    )
    assert job_catalog_search_box(
        {"label": "", "placeholder": "Search for job title or keywords", "type": "search"}
    )
    assert job_catalog_search_box({"label": "Search jobs", "type": "text"})
    assert not job_catalog_search_box(
        {"label": "Search for job title or keywords", "type": "text", "required": True}
    )
    assert not job_catalog_search_box({"label": "Search for job title or keywords", "type": "file"})
    assert not job_catalog_search_box(
        {"label": "Are you legally authorized to work in the United States?", "type": "text"}
    )
    assert not job_catalog_search_box({"label": "Search for a job in your area", "type": "text"})


def test_pick_qualified_city_commits_the_row_in_the_applicant_state():
    from jobpilot.widgets_ats import pick_qualified

    rows = [
        {"text": "Springfield, Washington, Maryland, United States"},
        {"text": "Springfield, Shelby, Tennessee, United States"},
    ]
    assert pick_qualified(rows, "Springfield", "MD") == rows[0]
    assert pick_qualified(rows, "Springfield", "Maryland") == rows[0]
    assert pick_qualified(rows, "Springfield", "") is None
    assert pick_qualified(rows, "Springfield", "TX") is None


def test_pick_qualified_refuses_two_rows_with_the_same_head():
    from jobpilot.widgets_ats import pick_qualified

    rows = [
        {"text": "Springfield, Sangamon, Maryland, United States"},
        {"text": "Springfield, Hampden, Maryland, United States"},
    ]
    assert pick_qualified(rows, "Springfield", "MD") is None


def test_pick_qualified_zip_matches_the_leading_code_only():
    from jobpilot.widgets_ats import pick_qualified

    rows = [
        {"text": "00000, Springfield, Maryland, United States"},
        {"text": "000000, Elsewhere, Maryland, United States"},
    ]
    assert pick_qualified(rows, "00000", "MD") == rows[0]
    assert pick_qualified(rows, "00000-1234", "MD") == rows[0]
    assert pick_qualified(rows[1:], "00000", "MD") is None


async def test_oracle_options_read_grid_rows_of_its_own_listbox_only(page):
    from jobpilot.widgets_ats import _options

    await page.set_content("""<input id=city role=combobox aria-controls=city-listbox popup=grid>
      <div id=city-listbox role=grid>
        <div role=row><div role=gridcell>Springfield, Washington, Maryland, United States</div></div>
        <div role=row><div role=gridcell>Springfield, Shelby, Tennessee, United States</div></div>
      </div>
      <div id=sibling-listbox role=grid><div role=row><div role=gridcell>Sibling popup row</div></div></div>""")
    texts = [o["text"] for o in await _options(page.locator("#city"), "oracle")]
    assert texts == [
        "Springfield, Washington, Maryland, United States",
        "Springfield, Shelby, Tennessee, United States",
    ]


async def test_automation_ids_lists_distinct_ids_in_dom_order(page):
    await page.set_content("""<main data-automation-id=shell><button data-automation-id=alpha></button>
      <a data-automation-id=alpha href=#></a><div data-automation-id=beta></div>
      <span data-automation-id=gamma></span></main><p>plain text</p>""")
    assert await widgets.automation_ids(page) == ["shell", "alpha", "beta", "gamma"]
    assert await widgets.automation_ids(page, 2) == ["shell", "alpha"]


async def test_automation_ids_empty_for_page_without_workday_markup(page):
    await page.set_content("<h1>Something unexpected</h1><div class=spinner></div>")
    assert await widgets.automation_ids(page) == []


async def test_unexplained_state_event_records_path_ids_and_title(page):
    from unittest.mock import Mock
    from jobpilot.workday import _emit_unexplained

    await page.set_content("""<title>Workday application review page with a very long title that is truncated</title>
      <div data-automation-id=pageHeader></div><div data-automation-id=pageFooter></div>""")
    engine = Mock()
    await _emit_unexplained(engine, page, 20)
    kind, message, data = engine.emit.call_args.args
    assert kind == "unexplained_state"
    assert message == "Workday page state was not recognized"
    assert data["path"] == "blank"  # set_content leaves the URL at about:blank: path only, no query or host
    assert data["automation_ids"] == ["pageHeader", "pageFooter"]
    assert data["title"] == "Workday application review page with a very long title that is truncated"[:60]
    assert len(data["title"]) <= 60
    assert data["iteration"] == 20


async def test_school_prompt_searches_the_institution_first(page):
    """Workday school lists name the institution ("University of Michigan"); a search for the profile's full
    "University of Michigan, Ann Arbor" never matched and waited out its 4 s before the fallback (School or
    University was the top Workday prompt failure on 10-17)."""
    import time

    await page.set_content("""<div data-automation-id=multiSelectContainer>
      <div data-automation-id=multiselectInputContainer id=school>
        <input onkeydown="if(event.key==='Enter'){search(this.value);event.preventDefault()}">
      </div></div><div id=menu role=listbox hidden></div>
      <script>
      window.queries=[];
      function search(q){window.queries.push(q);const m=document.getElementById('menu');m.hidden=false;
        m.innerHTML = q==='University of Michigan'
          ? '<div role=option data-automation-id=promptOption onclick="pick(this)">University of Michigan</div>'
            + '<div role=option data-automation-id=promptOption onclick="pick(this)">University of Michigan-Dearborn</div>'
          : '<div role=option>No Items.</div>';
      }
      function pick(e){document.getElementById('school').insertAdjacentHTML('beforeend',
        '<span data-automation-id=selectedItem>'+e.textContent+'</span>');e.parentElement.hidden=true;}
      </script>""")
    started = time.monotonic()
    chosen = await widgets.prompt(
        page.main_frame, page.locator("#school"), "University of Michigan, Ann Arbor", school_scope=True
    )
    assert chosen == "University of Michigan"
    assert await page.evaluate("window.queries") == ["University of Michigan"]
    assert time.monotonic() - started < 3


async def test_prompt_uses_settled_search_results_that_do_not_repeat_the_query(page):
    """Employer taxonomies rarely repeat the profile's wording ("Information Science" -> "Information Systems").
    Requiring the query text in a result waited out 4 s per search before the fallback; Field of Study averaged
    7-10 s on 10-17. Results that replaced the pre-search list and settled are the search result."""
    import time

    await page.set_content("""<div data-automation-id=multiSelectContainer>
      <div data-automation-id=multiselectInputContainer id=study>
        <input onclick="show(['Accounting','Biology'])" onkeydown="if(event.key==='Enter'){const q=this.value;window.queries.push(q);event.preventDefault();setTimeout(()=>show(q?['Information Systems','Information Technology']:['Accounting','Biology']),300)}">
      </div></div><div id=menu role=listbox hidden></div>
      <script>
      window.queries=[];
      function show(items){const m=document.getElementById('menu');m.hidden=false;
        m.innerHTML=items.map(t=>'<div role=option data-automation-id=promptOption onclick="pick(this)">'+t+'</div>').join('');}
      function pick(e){document.getElementById('study').insertAdjacentHTML('beforeend',
        '<span data-automation-id=selectedItem>'+e.textContent+'</span>');e.parentElement.hidden=true;}
      </script>""")
    choose = AsyncMock(return_value="Information Systems")
    started = time.monotonic()
    chosen = await widgets.prompt(page.main_frame, page.locator("#study"), "Information Science", choose)
    assert chosen == "Information Systems"
    assert await page.evaluate("window.queries") == ["Information Science"]
    assert time.monotonic() - started < 3


async def test_a_school_missing_from_the_list_commits_the_offered_other(page):
    """Lennar's Workday list has no "Nanyang Technological University" and shows "Other" only when searched; the
    Education 2 school stayed empty until the stall budget ran out (10-17). Search Other last; only an Other-style
    option or the exact school may commit."""
    await page.set_content("""<div data-automation-id=multiSelectContainer>
      <div data-automation-id=multiselectInputContainer id=school>
        <input onkeydown="if(event.key==='Enter'){search(this.value);event.preventDefault()}">
      </div></div><div id=menu role=listbox hidden></div>
      <script>
      window.queries=[];
      function search(q){window.queries.push(q);const m=document.getElementById('menu');m.hidden=false;
        const items = q==='Other' ? ['Other'] : q==='Nanyang' ? ['Nanyang University'] : ['No Items.'];
        m.innerHTML=items.map(t=>'<div role=option data-automation-id=promptOption onclick="pick(this)">'+t+'</div>').join('');}
      function pick(e){document.getElementById('school').insertAdjacentHTML('beforeend',
        '<span data-automation-id=selectedItem>'+e.textContent+'</span>');e.parentElement.hidden=true;}
      document.addEventListener('keydown',e=>{if(e.key==='Escape')document.getElementById('menu').hidden=true});
      </script>""")
    chosen = await widgets.prompt(
        page.main_frame, page.locator("#school"), "Nanyang Technological University", school_scope=True
    )
    assert chosen == "Other"
    assert (await page.evaluate("window.queries"))[-1] == "Other"


async def test_a_school_list_without_other_commits_its_not_listed_choice(page):
    # Lennar (Workday 2026-10-27): "If your school does not populate, please type School Not Listed". Searches for the
    # school and for "Other" returned "No Items." and the education row stalled.
    await page.set_content("""<div data-automation-id=multiSelectContainer>
      <div data-automation-id=multiselectInputContainer id=school>
        <input onclick="search()" onkeydown="if(event.key==='Enter'){search();event.preventDefault()}">
      </div></div><div id=menu role=listbox hidden></div>
      <script>
      function search(){let m=document.getElementById('menu');m.hidden=false;
        const q=document.querySelector('input').value.toLowerCase();
        m.innerHTML=q && 'school not listed'.includes(q) && q.length>3
          ? '<div role=option data-automation-id=promptOption onclick="pick(this)">School Not Listed</div>'
          : '<div role=option data-automation-id=promptOption>No Items.</div>';
      }
      function pick(e){document.getElementById('school').insertAdjacentHTML('beforeend',
        '<span data-automation-id=selectedItem>'+e.textContent+'</span>');e.parentElement.hidden=true;}
      </script>""")
    committed = await widgets.prompt(page.main_frame, page.locator("#school"), "Example Technological University",
                                     school_scope=True)
    assert committed == "School Not Listed"
