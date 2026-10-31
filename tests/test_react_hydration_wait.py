"""Greenhouse's job-boards form is server-rendered before React takes over (Esri, 2026-10-31): a fixed pause let the
engine type into a form that never hydrated, and the resume then 'was not accepted' on 14 jobs in a week, each
confirmed on a later try. The page is waited for, and reloaded once when it does not hydrate."""

from jobpilot import widgets

URL = "https://job-boards.greenhouse.io/embed/job_app?for=example&token=1"
HYDRATING = "<input name=first_name><script>setTimeout(() => { document.querySelector('input').__reactProps$a = {}; }, 300)</script>"
STUCK = "<input name=first_name>"


async def serve(page, bodies):
    loads = []

    async def handler(route):
        loads.append(route.request.url)
        await route.fulfill(content_type="text/html", body=bodies[min(len(loads), len(bodies)) - 1])

    await page.route("https://job-boards.greenhouse.io/**", handler)
    await page.goto(URL)
    return loads


async def test_a_page_that_hydrates_late_is_waited_for_not_reloaded(page):
    loads = await serve(page, [HYDRATING])
    assert await widgets.wait_react_hydrated(page, seconds=3) is True
    assert len(loads) == 1


async def test_a_page_that_never_hydrates_is_reloaded_once(page):
    loads = await serve(page, [STUCK, HYDRATING])
    assert await widgets.wait_react_hydrated(page, seconds=1) is True
    assert len(loads) == 2


async def test_a_page_that_stays_unhydrated_is_given_up_on_after_one_reload(page):
    loads = await serve(page, [STUCK])
    assert await widgets.wait_react_hydrated(page, seconds=1) is False
    assert len(loads) == 2
