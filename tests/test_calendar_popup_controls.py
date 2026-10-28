"""Axtria (JazzHR, 2026-10-27): the jQuery UI calendar of the mm/yyyy date box stayed open after the fill, and its
"Next" month link was taken for the application's Next 45 times (October 2030) while Submit Application was never
pressed. A typed date box's calendar popup is not page navigation."""

from jobpilot.forms import SCAN

PAGE = """<form id=form_submit_new_resume>
<label for=d>Completion date of your highest degree (mm/yyyy) *</label>
<input id=d class="form-control resumator-datepicker hasDatepicker" value="05/2026" required>
<a id=resumator-submit-resume class=btn href="#">Submit Application</a>
</form>
<div id=ui-datepicker-div class="ui-datepicker ui-widget" style="position:absolute;top:40px;left:10px;display:block">
  <div class=ui-datepicker-header>
    <a class=ui-datepicker-prev data-handler=prev title=Prev><span>Prev</span></a>
    <a class=ui-datepicker-next data-handler=next title=Next><span>Next</span></a>
    <div class=ui-datepicker-title>October 2026</div>
  </div>
  <table class=ui-datepicker-calendar><tr><td><a class=ui-state-default href="#">1</a></td>
    <td><a class=ui-state-default href="#">2</a></td><td><a class=ui-state-default href="#">3</a></td></tr></table>
</div>"""


async def test_an_open_calendar_offers_no_page_controls(page):
    await page.set_content(PAGE)
    data = await page.main_frame.evaluate(SCAN)
    labels = [b["label"] for b in data["buttons"]]
    assert "Submit Application" in labels
    assert not {"Prev", "Next", "1", "2", "3"} & set(labels)
