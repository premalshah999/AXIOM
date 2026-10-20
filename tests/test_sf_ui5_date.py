"""SuccessFactors application wizard (ASSA ABLOY dry run, 2026-10-15): its ``ui5-date-picker`` fields accept
typed text only in their own pattern ("MMM d, yyyy") and only on Enter, so "12/2025" left every
employment row red. The wizard also requires an end date for the current role."""

from datetime import date

from jobpilot import widgets
from jobpilot.answers import Resolver
from jobpilot.schemas import Profile, WorkEntry

PICKER = """
<script>
const MONTHS = ['Jan','Feb','Mar','Apr','May','Jun','Jul','Aug','Sep','Oct','Nov','Dec'];
// Real UI5 nesting: ui5-date-picker > (shadow) ui5-input > (shadow) input.ui5-input-inner
customElements.define('ui5-input', class extends HTMLElement {
  constructor() {
    super();
    this.attachShadow({mode: 'open'}).innerHTML = '<input class="ui5-input-inner" placeholder="MMM d, yyyy">';
  }
  get inner() { return this.shadowRoot.querySelector('input'); }
});
customElements.define('ui5-date-picker', class extends HTMLElement {
  constructor() {
    super();
    this.attachShadow({mode: 'open'}).innerHTML = '<ui5-input></ui5-input>';
    this.dateValue = null;
  }
  connectedCallback() {
    const input = this.shadowRoot.querySelector('ui5-input').inner;
    input.addEventListener('keydown', e => { if (e.key === 'Enter') this.commit(input.value); });
  }
  get value() { return this.getAttribute('value') || ''; }
  commit(text) {
    const m = text.match(/^([A-Z][a-z]{2}) (\\d{1,2}), (\\d{4})$/);
    if (m && MONTHS.includes(m[1])) {
      this.dateValue = new Date(+m[3], MONTHS.indexOf(m[1]), +m[2]);
      this.setAttribute('value', text);
      this.removeAttribute('value-state');
    } else {
      this.dateValue = null;
      this.setAttribute('value-state', 'Negative');
    }
  }
});
</script>
<label>Start Date <ui5-date-picker></ui5-date-picker></label>
"""


def test_cldr_patterns():
    assert widgets.format_ui5_date("MMM d, yyyy", "12", "01", "2025") == "Dec 1, 2025"
    assert widgets.format_ui5_date("MM/dd/yyyy", "7", "4", "2025") == "07/04/2025"
    assert widgets.format_ui5_date("dd.MM.yy", "3", "9", "2022") == "09.03.22"


async def test_scoped_picker_tags_are_recognized(page):
    # SuccessFactors registers scoped UI5 tags (ui5-date-picker-<suffix>).
    await page.set_content(PICKER.replace("ui5-date-picker", "ui5-date-picker-sf"))
    inner = page.locator("ui5-date-picker-sf input")
    assert await widgets.is_ui5wc_date(inner)
    assert await widgets.ui5wc_date(inner, "2023-08") == "Aug 1, 2023"


async def test_month_year_is_committed_in_the_picker_pattern(page):
    await page.set_content(PICKER)
    inner = page.locator("ui5-date-picker input")
    assert await widgets.is_ui5wc_date(inner)
    assert await widgets.ui5wc_date(inner, "12/2025") == "Dec 1, 2025"
    assert await page.locator("ui5-date-picker").get_attribute("value") == "Dec 1, 2025"


def _end_date(service, ats):
    profile = Profile(work=[WorkEntry(company="Acme", title="AI Engineer", start="2025-12", current=True)])
    resolver = Resolver(
        profile, service.db, service.config, job={"ats": ats, "url": "https://example.test/job"}
    )
    f = {
        "id": "e",
        "label": "End Date",
        "type": "text",
        "required": True,
        "value": "",
        "valid": True,
        "group": "Employment History 1",
        "options": [],
    }
    return resolver.local(f)


def test_current_role_end_date_is_today_only_on_successfactors(service):
    today = date.today()
    answer = _end_date(service, "successfactors")
    assert answer and answer.disposition == "answer" and answer.value == f"{today.month:02d}/{today.year}"
    skipped = _end_date(service, "workday")
    assert skipped is None or skipped.disposition == "skip"


def test_picker_display_matches_the_month_only_fact():
    field = {"label": "Start Date", "type": "text"}
    assert widgets.same_value(field, "Dec 1, 2025", "12/2025")
    assert widgets.same_value(field, "June 2, 2001", "06/02/2001")
    assert not widgets.same_value(field, "Dec 1, 2025", "11/2025")
    assert not widgets.same_value(field, "June 3, 2001", "06/02/2001")
