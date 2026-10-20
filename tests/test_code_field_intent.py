"""Autofill metadata cannot override an application's actual question."""
from types import SimpleNamespace
import pytest
from jobpilot.adapters.base import Adapter
from jobpilot.email_browser import EmailBrowser


def observation(label, *, text='Application', autocomplete='one-time-code'):
    return {'text': text, 'fields': [{'id': 'field', 'label': label, 'type': 'text',
                                    'maxlength': -1, 'autocomplete': autocomplete}]}


@pytest.mark.parametrize('label', ['Employee ID(if applicable)', 'Referral code', 'Postal Code',
                                  'Salary expectations', 'Have you implemented OTP authentication?'])
@pytest.mark.parametrize('armed', [False, True])
def test_application_question_is_not_email_challenge_even_after_submit(label, armed):
    obs = observation(label, text='Application. You may receive a verification code by email.')
    browser = object.__new__(EmailBrowser)
    browser.engine = SimpleNamespace(armed=armed)
    assert browser.state(obs) == (None, [])
    assert object.__new__(Adapter).code_fields(obs) == []


@pytest.mark.parametrize('label,text,expected', [
    ('Verification code', 'Verify email', True),
    ('* Passcode:', 'Confirm identity', True),
    ('Unlabelled field', 'Enter your code', True),
    ('Unlabelled field', 'Application', False),
])
def test_explicit_codes_and_instruction_bound_unlabelled_inputs_remain_supported(label, text, expected):
    obs = observation(label, text=text)
    browser = object.__new__(EmailBrowser)
    browser.engine = SimpleNamespace(armed=False)
    assert bool(browser.state(obs)[1]) is expected
    assert bool(object.__new__(Adapter).code_fields(obs)) is expected
