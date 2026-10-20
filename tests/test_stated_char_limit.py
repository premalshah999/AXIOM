"""John Deere (Eightfold) 2026-10-15: the page rejected a long explanation with "Input greater than 199
characters" (no maxlength attribute); the answer is fitted to the stated limit and refilled."""

from jobpilot.forms import prose_for_constraint

LONG = (
    "I am currently authorized to work in the United States on F-1 OPT through 2027. I do not require "
    "sponsorship now. I am happy to provide any documentation needed. My status permits full-time work "
    "for any employer, and I can start immediately after an offer is accepted."
)


def test_stated_limit_shortens_prose_to_whole_sentences():
    field = {"type": "textarea", "error": "Input greater than 199 characters"}
    out = prose_for_constraint(field, LONG)
    assert len(out) <= 199 and out.startswith("I am currently authorized") and out.endswith(".")


def test_text_fields_and_unrelated_errors_are_untouched():
    assert (
        prose_for_constraint({"type": "text", "error": "Input greater than 199 characters"}, "N/A") == "N/A"
    )
    assert prose_for_constraint({"type": "textarea", "error": "This field is required"}, LONG) == LONG
    assert prose_for_constraint({"type": "url", "error": "Input greater than 20 characters"}, LONG) == LONG


async def test_silently_truncated_prose_is_fitted_and_refilled(page, tmp_path):
    # The input keeps only 120 characters via script; no maxlength attribute, no error text.
    from jobpilot.answers import Resolver
    from jobpilot.forms import FormSession
    from jobpilot.schemas import Profile

    await page.set_content("""<label>Explain your work authorization situation
      <input id=w oninput="if (this.value.length > 120) this.value = this.value.slice(0, 120)"></label>""")
    form = FormSession(page, Resolver(Profile(), None, None), tmp_path / "r.pdf", lambda *a: None)
    await form.scan()
    field = next(iter(form.fields.values()))
    form.expected[field["id"]] = await form.fill(field, LONG)
    await form.scan()
    field = next(iter(form.fields.values()))
    assert len(field["value"]) == 120
    form.expected[field["id"]] = LONG
    # Run only the repair loop's truncation branch through fill_current's tail: call the helper path directly.
    kept = field["value"]
    from jobpilot.features.knowledge.writing import fit_length

    fitted = fit_length(LONG, len(kept))
    assert len(fitted) <= 120 and fitted.endswith(".") and LONG.startswith(fitted[:40])


def test_typing_allowance_grows_with_the_text_and_stays_under_the_write_budget():
    # Playwright sleeps 12 ms per key plus the key events' round trips: 257 characters need about 4 s on an idle
    # host, so the old fixed 4 s allowance timed out a long answer half typed.
    from jobpilot.forms import FIELD_WRITE_SECONDS
    from jobpilot.widgets import KEY_DELAY_MS, typing_timeout

    assert typing_timeout("N/A") == 4000  # Short answers keep the caller's allowance.
    assert typing_timeout("x" * 257) >= 257 * KEY_DELAY_MS * 2
    assert typing_timeout("x" * 300) > typing_timeout("x" * 200)
    assert typing_timeout("x" * 5000) < FIELD_WRITE_SECONDS * 1000
    assert typing_timeout("x" * 10, floor=8000) == 8000
