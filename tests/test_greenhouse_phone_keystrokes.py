"""Greenhouse phone (fieldset.phone-input) keeps state from key events only.

2026-10-13: seven jobs showed a number before submit, then "Phone number is too short" on an
empty field after the click, because a value write never reached the widget's state.
"""

from jobpilot.answers import Resolver
from jobpilot.forms import FormSession
from jobpilot.schemas import Profile

WIDGET = """
<form><fieldset class="phone-input"><legend>Phone</legend>
  <div class="phone-input__phone"><label for="phone">Phone</label><input id="phone" type="text"></div>
</fieldset><button id="rerender" type="button">rerender</button></form>
<script>
  // Like a controlled React input: state follows key events; a re-render shows the state.
  let state = '';
  const input = document.getElementById('phone');
  input.addEventListener('keydown', e => {
    if (/^[\\d+]$/.test(e.key)) state += e.key;
    if (e.key === 'Backspace') state = state.slice(0, -1);
  });
  input.addEventListener('input', e => { if (!e.isTrusted || e.inputType === 'insertReplacementText' || !e.data) {} });
  document.getElementById('rerender').onclick = () => { input.value = state; };
</script>
"""


async def test_phone_in_greenhouse_fieldset_is_typed_so_it_survives_a_rerender(page, tmp_path):
    await page.set_content(WIDGET)
    form = FormSession(page, Resolver(Profile(), None, None), tmp_path / "resume.pdf", lambda *a: None)
    await form.scan()
    phone = next(f for f in form.fields.values() if f["label"] == "Phone")
    await form.fill(phone, "+1 (301) 555-0142")
    await page.click("#rerender")
    assert await page.input_value("#phone") == "+13015550142"


def test_phone_comparison_ignores_the_country_prefix_and_format():
    from jobpilot import widgets

    phone = {"type": "tel", "label": "Phone"}
    assert widgets.same_value(phone, "+1 555-555-0100", "(555) 555-0100")
    assert not widgets.same_value(phone, "+1 240-501-3451", "(555) 555-0100")


async def test_prefilled_greenhouse_phone_is_retyped_once_per_run(page, tmp_path, service):
    async def route(request):
        await request.fulfill(
            status=200,
            content_type="text/html",
            body=WIDGET.replace(
                '<input id="phone" type="text">', '<input id="phone" type="tel" value="(301) 555-0142">'
            ),
        )

    await page.context.route("**/*", route)
    await page.goto("https://job-boards.greenhouse.io/acme/jobs/1")
    resolver = Resolver(
        Profile(phone="(301) 555-0142", phone_country_code="+1 (United States)"), service.db, service.config
    )
    form = FormSession(page, resolver, tmp_path / "resume.pdf", lambda *a: None)
    await form.scan()
    phone = next(f for f in form.fields.values() if f["label"] == "Phone")
    from jobpilot.schemas import Answer

    # Production resolves the stored phone; this fixture's resolver has no answer model.
    resolver.local = lambda f: Answer(
        field_id=f["id"], value="(301) 555-0142", disposition="answer", evidence_ids=["profile:phone"]
    )
    assert form.preselected(phone, False) is False  # retype the stale prefilled value
    assert form.preselected(phone, False) is True  # once per run


async def test_greenhouse_phone_is_retyped_right_before_submit(page, tmp_path, service):
    async def route(request):
        await request.fulfill(
            status=200,
            content_type="text/html",
            body=WIDGET.replace(
                '<input id="phone" type="text">', '<input id="phone" type="tel" value="(301) 555-0142">'
            ),
        )

    await page.context.route("**/*", route)
    await page.goto("https://job-boards.greenhouse.io/acme/jobs/1")
    resolver = Resolver(
        Profile(phone="(301) 555-0142", phone_country_code="+1 (United States)"), service.db, service.config
    )
    events = []
    form = FormSession(page, resolver, tmp_path / "resume.pdf", lambda *a: events.append(a))
    await form.refresh_greenhouse_phone()
    await page.click("#rerender")
    assert await page.input_value("#phone") == "+13015550142"
    assert any(e[0] == "phone_typed" and e[2]["matches"] for e in events)


async def test_stale_site_error_after_a_verified_retype_does_not_block_resubmit(page, tmp_path, service):
    # After a refused submit Greenhouse keeps "Phone number is too short" until the next submit.
    async def route(request):
        await request.fulfill(
            status=200,
            content_type="text/html",
            body=WIDGET.replace(
                '<input id="phone" type="text">',
                '<input id="phone" type="tel" aria-invalid="true" value="(301) 555-0142">',
            ),
        )

    await page.context.route("**/*", route)
    await page.goto("https://job-boards.greenhouse.io/acme/jobs/1")
    resolver = Resolver(
        Profile(phone="(301) 555-0142", phone_country_code="+1 (United States)"), service.db, service.config
    )
    form = FormSession(page, resolver, tmp_path / "resume.pdf", lambda *a: None)
    await form.scan()
    phone = next(f for f in form.fields.values() if f["label"] == "Phone")
    await form.fill(phone, "(301) 555-0142")
    form.expected[phone["id"]] = "(301) 555-0142"
    assert phone["id"] in form.typed_phones
    report = await form.verify()
    assert not any("Phone" in p for p in report["problems"]), report["problems"]


def test_unlabeled_wotc_radios_answer_no_only_on_a_tax_credit_page(tmp_path):
    from jobpilot.schemas import Profile as P

    resolver = Resolver(P(facts={"tax_credit_screening_no": True}), None, None)
    form = FormSession(None, resolver, tmp_path / "resume.pdf", lambda *a: None)
    radio = {"id": "r", "label": "", "type": "radiogroup", "required": True, "options": ["Yes", "No"]}
    form.last_observation = {"text": "Work Opportunity Tax Credit questionnaire"}
    assert form.wotc_answer(radio).value == "No"
    assert form.wotc_answer({**radio, "label": "Are you under the age of 40?"}) is None
    form.last_observation = {"text": "Application questions"}
    assert form.wotc_answer(radio) is None
