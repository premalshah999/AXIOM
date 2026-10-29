from jobpilot.answers import Resolver
from jobpilot.forms import FormSession
from jobpilot.schemas import Profile


async def test_year_only_history_fields_do_not_borrow_another_sections_school(page, service, tmp_path):
    await page.set_content('''<main><div>
      <section><h2>Education</h2>
        <label>University<select><option>Other (not listed)</option></select></label>
        <label>University name<input value="North University"></label>
        <label>Start year<input type=number></label>
        <label>Graduation year<input type=number></label>
      </section>
      <section><h2>Experience</h2>
        <label>Most recent company<input value="Example Labs"></label>
        <label>Role<input value="Engineer"></label>
        <label>Start year<input type=number></label>
      </section>
    </div></main>''')
    profile = Profile.model_validate({
        'education': [{'school': 'North University', 'degree': 'Master of Science', 'start': '2023-08', 'end': '2025-05'}],
        'work': [{'company': 'Example Labs', 'title': 'Engineer', 'start': '2025-06', 'current': True}],
    })
    resolver = Resolver(profile, service.db, service.config)
    form = FormSession(page, resolver, tmp_path / 'unused.pdf', lambda *a: None)
    fields = (await form.scan())['fields']
    years = [f for f in fields if f['label'] == 'Start year']
    assert [(f['history_kind'], f['group']) for f in years] == [
        ('education', 'Education 1'), ('work', 'Work Experience 1')]
    assert years[1]['history_identity'] == {'company': 'Example Labs', 'title': 'Engineer'}
    assert not years[1]['history_identity'].get('school')

    answers = [resolver._entry_answer(f, f['label'].lower()) for f in years]
    assert [a.value for a in answers] == ['2023', '2025']
    graduation = next(f for f in fields if f['label'] == 'Graduation year')
    assert resolver._entry_answer(graduation, 'graduation year').value == '2025'
