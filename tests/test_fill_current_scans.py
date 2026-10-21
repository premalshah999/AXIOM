"""fill_current does not scan a page it has just scanned.

On a page with nothing to write it took five scans (seven when the profile accepts terms dialogs, because the dialog
check scans and the plain scan after it read the same page): a look at the start, a look after the upload step, a
look at the top of the answer loop before anything had been written, one more after the loop ended because no
question was left to ask, and the read-back. The navigator calls fill_current on every step, and most steps of a
multi-page application have nothing left to fill, so each paid several full scans (an evaluate of the scan script in
every frame) for no new information. A scan is still taken after anything that writes.
"""

import pytest

from jobpilot.answers import Resolver
from jobpilot.forms import FormSession
from jobpilot.schemas import Profile

# A page with no question on it: a notice and a button.
PAGE = "<h1>Review your application</h1><p>Nothing to answer on this page.</p><button type=button>Next</button>"


def counted(form):
    calls = []
    original = form.scan

    async def scan(*args, **kwargs):
        calls.append(1)
        return await original(*args, **kwargs)

    form.scan = scan
    return calls


@pytest.mark.parametrize("accept_terms", [False, True])
async def test_a_page_with_nothing_to_ask_is_scanned_three_times_not_five(page, service, tmp_path, accept_terms):
    resume = tmp_path / "r.pdf"
    resume.write_bytes(b"%PDF")
    await page.set_content(PAGE)
    profile = Profile(accept_all_application_terms=accept_terms)
    form = FormSession(page, Resolver(profile, service.db, service.config), resume, lambda *a, **k: None)
    scans = counted(form)
    await form.fill_current()
    # The call's first look, the look after the upload step and the read-back's look. The answer loop, which finds
    # nothing to ask, and the code after it need no look of their own: nothing has been written since the one before.
    assert len(scans) == 3, len(scans)


async def test_a_page_that_was_written_is_still_scanned_after_the_writes(page, service, tmp_path):
    resume = tmp_path / "r.pdf"
    resume.write_bytes(b"%PDF")
    await page.set_content("<label for=p>Phone</label><input id=p required type=tel>")
    profile = Profile(phone="2405550100")
    form = FormSession(page, Resolver(profile, service.db, service.config), resume, lambda *a, **k: None)
    scans = counted(form)
    await form.fill_current()
    assert await page.locator("#p").input_value() != ""
    # first look, look after the upload step, the look after the phone write, and the read-back.
    assert len(scans) >= 4, len(scans)
