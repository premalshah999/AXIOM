"""Morgan Stanley (Eightfold) 2026-10-19: Apply Now leads to ms.wd5.myworkdayjobs.com, and the generic navigator looped on
Workday's duplicate Create Account form six runs in a row. Workday's own adapter takes over once the page is Workday."""

from types import SimpleNamespace

from jobpilot.browser import BrowserEngine

WORKDAY = "https://ms.wd5.myworkdayjobs.com/External/job/NY/Associate--Software-Engineer_JR036259/apply?src=Eightfold"


def engine(url, armed=False, ats="eightfold"):
    e = object.__new__(BrowserEngine)
    e.armed, e.job, e.page = armed, {"ats": ats}, SimpleNamespace(url=url)
    e.fallback_mail_adapter = SimpleNamespace(id=ats)
    e.emitted, e.started = [], []
    e.emit = lambda kind, message, data=None: e.emitted.append(kind)

    async def run_adapters(target):
        e.started.append(target)
        return {"state": "confirmed", "reason": "Website confirmation captured"}

    e.run_adapters = run_adapters
    return e


async def test_workday_takes_over_once_after_an_eightfold_apply_link():
    e = engine(WORKDAY)
    assert (await e.navigator_handoff())["state"] == "confirmed"
    assert e.started == ["workday"] and e.emitted == ["handoff"]
    assert await e.navigator_handoff() is None  # Once per run.


async def test_no_handoff_after_the_commit_or_on_the_same_platform():
    assert await engine(WORKDAY, armed=True).navigator_handoff() is None
    assert await engine(WORKDAY, ats="workday").navigator_handoff() is None
    assert await engine("https://morganstanley.eightfold.ai/careers/job/1").navigator_handoff() is None
    assert await engine("https://boards.greenhouse.io/acme/jobs/1").navigator_handoff() is None  # No dedicated adapter.


async def test_an_adapter_without_a_way_forward_leaves_the_navigator_in_charge():
    e = engine(WORKDAY)

    async def fallback(target):
        return {"fallback": True, "reason": "No way forward from this page"}

    e.run_adapters = fallback
    assert await e.navigator_handoff() is None


async def test_the_run_adopts_the_workday_posting_so_its_account_flow_accepts_the_employer():
    # Morgan Stanley 2026-10-19: Workday's account step refused "account origin does not match this employer".
    e = engine(WORKDAY)
    e.job = {"ats": "eightfold", "url": "https://morganstanley.eightfold.ai/careers/job/549797629967"}
    await e.navigator_handoff()
    assert e.job["ats"] == "workday"
    assert e.job["url"] == "https://ms.wd5.myworkdayjobs.com/External/job/NY/Associate--Software-Engineer_JR036259"
    assert e.handoff_origin == "https://ms.wd5.myworkdayjobs.com"
