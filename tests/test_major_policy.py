"""Owner policy for majors (2026-10-13): the recorded major when offered, else Computer Science. On 10-17 Slate Auto's
Workday search for "Data Science" returned only "Data Processing" and a model guess committed it. A major is now the
recorded one, an offered name made only of the recorded major's words, or Computer Science; never an unrelated
search result."""

from unittest.mock import AsyncMock

from jobpilot.answers import Resolver
from jobpilot.schemas import Profile

PROFILE = {
    "education": [
        {"school": "University of Maryland, College Park", "degree": "Master of Science", "field_of_study": "Data Science"},
        {"school": "Gujarat Technological University", "degree": "BE",
         "field_of_study": "Information & Communication Technology"},
    ]
}


def major(row):
    return {"id": f"m{row}", "label": "Field of Study", "type": "prompt", "group": f"Education {row}", "section": ""}


def resolver(service):
    return Resolver(Profile.model_validate(PROFILE), service.db, service.config)


def test_an_unrelated_search_result_is_not_the_recorded_major(service):
    r = resolver(service)
    assert r.policy_choice(major(1), ["Data Processing"]) is None
    assert r.policy_choice(major(1), ["Data Processing", "Computer Science"]) == "Computer Science"
    assert r.policy_choice(major(1), ["Data Science", "Computer Science"]) == "Data Science"


def test_an_offered_name_made_of_the_recorded_majors_words_is_that_major(service):
    r = resolver(service)
    assert r.policy_choice(major(2), ["Accounting", "Information Technology"]) == "Information Technology"
    # Words outside the recorded major make it a different major.
    assert r.policy_choice(major(2), ["Information Systems Management", "Accounting"]) is None


async def test_a_workday_major_search_commits_policy_answers_only(page, service, tmp_path):
    from jobpilot.forms import FormSession

    await page.set_content("""<div data-automation-id="formField-fieldOfStudy"><label for=fos>Field of Study</label>
      <div data-automation-id=multiSelectContainer>
      <div data-automation-id=multiselectInputContainer id=fos-box>
        <input id=fos onkeydown="if(event.key==='Enter'){search(this.value);event.preventDefault()}">
      </div></div></div><div id=menu role=listbox hidden></div>
      <script>
      function search(q){const m=document.getElementById('menu');m.hidden=false;
        const items = /computer/i.test(q) ? ['Computer Science','Computer Engineering'] : /data/i.test(q) ? ['Data Processing'] : ['No Items.'];
        m.innerHTML=items.map(t=>'<div role=option data-automation-id=promptOption onclick="pick(this)">'+t+'</div>').join('');}
      function pick(e){document.getElementById('fos-box').insertAdjacentHTML('beforeend',
        '<span data-automation-id=selectedItem>'+e.textContent+'</span>');e.parentElement.hidden=true;}
      // Like Workday's prompt, Escape or an outside click closes the results.
      document.addEventListener('keydown',e=>{if(e.key==='Escape')document.getElementById('menu').hidden=true});
      document.addEventListener('click',e=>{if(!e.target.closest('#menu,#fos-box'))document.getElementById('menu').hidden=true});
      </script>""")
    r = resolver(service)
    r.resolve = AsyncMock(side_effect=AssertionError("no model guess for a major"))
    form = FormSession(page, r, tmp_path / "unused.pdf", lambda *a: None)
    field = next(f for f in (await form.scan())["fields"] if f["label"] == "Field of Study")
    field["group"] = "Education 1"
    assert await form.fill(field, "Data Science") == "Computer Science"
