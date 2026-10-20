from types import SimpleNamespace
from unittest.mock import AsyncMock
import pytest
from jobpilot.browser_contracts import missing_posting_notice
from jobpilot.adapters.base import Adapter

JOB = 'https://careers-example.icims.com/jobs/42/software-engineer/job'


def observation():
    return {'url':'https://careers-example.icims.com/jobs/search?notFound=1',
            'headings':['Error: The requested job could not be found.', 'Search Results Page 1 of 2'],
            'errors':[], 'fields':[{'type':'text','label':'Start your job search here'},
                                   {'type':'select','label':'Location'}], 'controls':[], 'text':''}


def test_missing_posting_with_search_filters_is_not_an_application():
    assert missing_posting_notice(observation(), JOB)


@pytest.mark.parametrize('change', ['other_host', 'body_only', 'busy', 'dialog', 'modal', 'normal'])
def test_missing_notice_requires_current_employer_page_evidence(change):
    obs = observation()
    if change == 'other_host': obs['url']='https://other.example/jobs/search'
    elif change == 'body_only': obs['text']=obs['headings'].pop(0)
    elif change == 'busy': obs['busy']=True
    elif change == 'dialog': obs['dialog']=True
    elif change == 'modal': obs['dialogs']=[{'control_ids':['sign_in']}]
    else: obs['headings']=['Software Engineer', 'Search Results']
    assert not missing_posting_notice(obs, JOB)


async def test_adapter_stops_before_classification_filling_or_model_recovery():
    obs = observation()
    adapter = object.__new__(Adapter)
    adapter.e = SimpleNamespace(result=None, form=SimpleNamespace(
        settle=AsyncMock(), scan=AsyncMock(return_value=obs)))
    adapter.job={'url':JOB}
    adapter.committed=False
    adapter.dismiss_overlays=AsyncMock()
    adapter.captcha_open=AsyncMock(return_value=False)
    def unexpected(*args):
        pytest.fail('Missing posting entered form classification')
    adapter.classify_page=unexpected
    assert (await adapter._drive())['state']=='closed'
