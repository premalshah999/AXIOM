"""Quality review cannot spend write attempts before a field is editable."""
from types import SimpleNamespace
import pytest
from jobpilot import answer_quality
from jobpilot.answers import Resolver
from jobpilot.schemas import Profile, Answer


@pytest.fixture
def audit_fixture(service, monkeypatch):
    service.config.answer_quality_enabled = True
    field = {'id': 'name', 'label': 'First name', 'type': 'text', 'required': True,
             'value': 'Wrong', 'valid': True, 'editable': False}
    answer = Answer(field_id='name', disposition='answer', value='Alex', evidence_ids=['profile:name'])
    monkeypatch.setattr(answer_quality, 'candidates', lambda *args, **kwargs: [dict(field)])
    monkeypatch.setattr(answer_quality, 'local_corrections', lambda *args: {'name': (answer, 'confirmed_fact_mismatch')})
    form = SimpleNamespace(resolver=Resolver(Profile(name='Alex Example'), service.db, service.config),
                           failed_strategies={}, expected={}, expected_slots={}, writes=[])
    async def scan(): return {}
    async def rematch(original): return dict(field)
    async def fill(current, value):
        form.writes.append(value)
        raise ValueError('Fixture widget rejected write')
    form.scan, form.rematch, form.fill = scan, rematch, fill
    form.emit = lambda *args: None
    form.field_key = lambda current: ('name',)
    form.record_answer = lambda *args, **kwargs: None
    return form, field


async def test_repeated_review_of_earlier_step_preserves_repair_attempts(audit_fixture):
    form, field = audit_fixture
    for _ in range(3):
        result = await answer_quality.audit_form(form)
        assert not result['ok']
        assert result['pending'][0]['repair_action'] == 'revisit_history_row'
        assert result['pending'][0]['retryable']
    assert not form.writes
    assert not form.failed_strategies['__quality_repair_attempts__']
    field['editable'] = True
    await answer_quality.audit_form(form)
    assert form.writes == ['Alex']
    assert list(form.failed_strategies['__quality_repair_attempts__'].values()) == [1]


async def test_actual_rejected_writes_still_exhaust_two_repair_attempts(audit_fixture):
    form, field = audit_fixture
    field['editable'] = True
    for _ in range(2):
        result = await answer_quality.audit_form(form)
        assert result['pending'][0]['retryable']
    result = await answer_quality.audit_form(form)
    assert not result['ok'] and result['pending'][0]['retryable'] is False
    assert form.writes == ['Alex', 'Alex']


async def test_stale_observation_does_not_charge_a_write(audit_fixture):
    form, field = audit_fixture
    field['editable'] = True
    async def missing(original): raise ValueError('Question changed while filling')
    form.rematch = missing
    for _ in range(3):
        result = await answer_quality.audit_form(form)
        assert result['pending'][0]['retryable']
    assert not form.writes and not form.failed_strategies['__quality_repair_attempts__']
