"""Return to a flagged field's observed scope, across ATS page names and history rows."""
from collections import Counter
from types import SimpleNamespace
import pytest
from jobpilot.adapters.base import Adapter
from jobpilot.adapters.workday import Workday


def blocker(question='Base pay expectation', **scope):
    return {'report': {'pending': [{'question': question, 'type': 'text',
                                   'repair_action': 'revisit_history_row', 'scope': scope}]}}


def observation(name, fields=(), back=True):
    return {'url': 'https://employer.test/apply/'+name, 'headings': [name], 'fields': list(fields),
            'controls': [{'id': 'back', 'label': 'Back', 'automation_id': 'pageFooterBackButton'}] if back else []}


def field(label, **scope):
    return {'label': label, 'type': 'text', **scope}


def driver(pages, cls=Workday):
    adapter = object.__new__(cls)
    adapter.committed, adapter.attempts = False, Counter()
    adapter.events, adapter.clicks, adapter.index = [], [], 0
    adapter.emit = lambda kind, *args: adapter.events.append(kind)
    async def scan(): return pages[adapter.index]
    async def settle(**kwargs): pass
    async def click(control):
        adapter.clicks.append(control['id'])
        adapter.index = min(adapter.index+1, len(pages)-1)
    adapter.click = click
    adapter.e = SimpleNamespace(armed=False, form=SimpleNamespace(scan=scan, settle=settle))
    return adapter


@pytest.mark.parametrize('cls', [Workday, Adapter])
async def test_finds_actual_question_without_assuming_application_questions_page(cls):
    pages = [observation('Review'), observation('Disclosures'),
             observation('Renamed screening', [field('Base pay expectation')])]
    adapter = driver(pages, cls)
    assert await adapter.revisit_history(blocker())
    assert adapter.clicks == ['back', 'back'] and adapter.events == ['quality_repair']
    assert not await adapter.revisit_history(blocker())


async def test_history_scope_prevents_stopping_at_same_label_in_wrong_row():
    pages = [observation('Review'), observation('Questions', [field('Degree', group='Education 1')]),
             observation('Experience', [field('Degree', group='Education 2')])]
    adapter = driver(pages)
    assert await adapter.revisit_history(blocker('Degree', group='Education 2'))
    assert adapter.index == 2


async def test_ambiguous_fields_or_unchanged_back_action_do_not_loop():
    adapter = driver([observation('Review', [field('Degree'), field('Degree')])])
    assert not await adapter.revisit_history(blocker('Degree'))
    assert adapter.clicks == ['back']
    assert not await adapter.revisit_history(blocker('Degree'))
    assert adapter.clicks == ['back']


async def test_different_earlier_questions_each_get_a_bounded_visit():
    adapter = driver([observation('Questions', [field('Base pay expectation')]),
                      observation('Experience', [field('Degree', group='Education 1')])])
    assert await adapter.revisit_history(blocker())
    assert await adapter.revisit_history(blocker('Degree', group='Education 1'))
    assert adapter.index == 1


@pytest.mark.parametrize('guard', ['committed', 'armed'])
async def test_never_navigates_back_after_submission(guard):
    adapter = driver([observation('Review')])
    if guard == 'committed': adapter.committed = True
    else: adapter.e.armed = True
    assert not await adapter.revisit_history(blocker())
    assert not adapter.clicks


async def test_missing_back_control_leaves_supported_correction_blocked():
    adapter = driver([observation('Review', back=False)])
    assert not await adapter.revisit_history(blocker())
    assert not adapter.clicks


async def test_owned_browser_back_reopens_correct_section_without_submitting(page, tmp_path):
    from jobpilot.answers import Resolver
    from jobpilot.forms import FormSession
    from jobpilot.schemas import Profile
    from jobpilot.adapters.base import Step
    await page.set_content('''<form onsubmit="window.submitted=true;return false">
      <section id=review><h2>Review</h2><button type=button
        onclick="document.getElementById('review').hidden=true;document.getElementById('history').hidden=false">Back</button>
        <button type=submit>Submit Application</button></section>
      <section id=history hidden><h2>Education 2</h2><label for=degree>Degree</label>
        <input id=degree value="Bachelor's"></section></form>''')
    form = FormSession(page, Resolver(Profile(), None, None), tmp_path/'resume.pdf', lambda *args: None)
    adapter = object.__new__(Adapter)
    adapter.e = SimpleNamespace(armed=False, form=form)
    adapter.committed, adapter.last_step, adapter.attempts = False, Step.REVIEW, Counter()
    adapter.emit = lambda *args: None
    async def dismiss(): pass
    adapter.dismiss_overlays = dismiss
    assert await adapter.revisit_history(blocker('Degree', section='Education 2'))
    assert await page.locator('#degree').is_visible()
    assert not await page.evaluate('!!window.submitted')
