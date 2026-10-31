from types import SimpleNamespace
from jobpilot.widgets_ats import read_combos


async def test_many_oracle_values_read_in_one_call_with_same_results(page):
    await page.set_content(''.join(f'<input role=combobox class=cx-select-input data-jp-id=p{i} value="Choice {i}">' for i in range(30))
                           + '<input role=combobox data-jp-id=foreign value="Unrelated">')
    calls=[]
    async def evaluate(*args):
        calls.append(1)
        return await page.evaluate(*args)
    frame=SimpleNamespace(url='https://acme.fa.us2.oraclecloud.com/hcmUI/CandidateExperience/en/sites/CX_1/job/1', evaluate=evaluate)
    values=await read_combos(frame,[f'p{i}' for i in range(30)]+['foreign'])
    assert len(calls)==1 and len(values)==30
    for i in (0,15,29):
        assert values[f'p{i}']=={'family':'oracle','value':f'Choice {i}'}
    assert 'foreign' not in values


async def test_dayforce_reads_owned_selection_not_search_query(page):
    await page.set_content('''<div class=ant-select><span class=ant-select-selection-item title="United States">US</span>
      <input role=combobox class=ant-select-selection-search-input data-jp-id=country value=Canada></div>
      <div class=ant-select><span class=ant-select-selection-item>Other field</span>
      <input role=combobox class=ant-select-selection-search-input data-jp-id=other></div>''')
    frame=SimpleNamespace(url='https://jobs.dayforcehcm.com/en-US/acme/jobs/1', evaluate=page.evaluate)
    result=await read_combos(frame,['country'])
    assert result=={'country':{'family':'dayforce','value':'United States'}}


async def test_untrusted_host_cannot_enable_provider_override():
    async def evaluate(*args):
        raise AssertionError('Unsupported host must not run provider readback')
    frame=SimpleNamespace(url='https://evil.example/hcmUI/CandidateExperience/', evaluate=evaluate)
    assert await read_combos(frame,['country'])=={}


async def test_shadow_control_keeps_same_provider_readback(page):
    await page.set_content('<div id=host></div>')
    await page.evaluate('''host.attachShadow({mode:'open'}).innerHTML='<input role=combobox class=cx-select-input data-jp-id=inside value="Maryland">' ''')
    frame=SimpleNamespace(url='https://acme.oraclecloud.com/hcmUI/CandidateExperience/', evaluate=page.evaluate)
    assert (await read_combos(frame,['inside']))['inside']['value']=='Maryland'
