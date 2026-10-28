"""Majors: the recorded major when offered, else the profile's fallback major (answer_rules.major_fallback). On 10-17
Slate Auto's Workday search for "Data Science" returned only "Data Processing" and a model guess committed it. A major
is now the recorded one, an offered name made only of the recorded major's words, or the profile's fallback major (none
unless the profile sets one); never an unrelated search result."""

from unittest.mock import AsyncMock

import pytest

from jobpilot.answers import Resolver
from jobpilot.schemas import Profile

PROFILE = {
    "education": [
        {"school": "University of Michigan, Ann Arbor", "degree": "Master of Science", "field_of_study": "Data Science"},
        {"school": "Nanyang Technological University", "degree": "BE",
         "field_of_study": "Information & Communication Technology"},
    ]
}


def major(row):
    return {"id": f"m{row}", "label": "Field of Study", "type": "prompt", "group": f"Education {row}", "section": ""}


def resolver(service, fallback="Computer Science"):
    rules = {"answer_rules": {"major_fallback": fallback}} if fallback else {}
    return Resolver(Profile.model_validate({**PROFILE, **rules}), service.db, service.config)


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


def test_without_a_fallback_major_no_other_major_is_chosen(service):
    r = resolver(service, fallback="")
    assert r.policy_choice(major(1), ["Data Processing", "Computer Science"]) is None
    assert r.policy_choice(major(1), ["Data Science", "Computer Science"]) == "Data Science"


async def test_a_workday_major_search_without_a_fallback_major_commits_nothing(page, service, tmp_path):
    from jobpilot.forms import FormSession

    await page.set_content("""<div data-automation-id="formField-fieldOfStudy"><label for=fos>Field of Study</label>
      <div data-automation-id=multiSelectContainer>
      <div data-automation-id=multiselectInputContainer id=fos-box>
        <input id=fos onkeydown="if(event.key==='Enter'){search(this.value);event.preventDefault()}">
      </div></div></div><div id=menu role=listbox hidden></div>
      <script>
      function search(q){const m=document.getElementById('menu');m.hidden=false;
        const items = /computer/i.test(q) ? ['Computer Science','Computer Engineering'] : ['No Items.'];
        m.innerHTML=items.map(t=>'<div role=option data-automation-id=promptOption onclick="pick(this)">'+t+'</div>').join('');}
      function pick(e){document.getElementById('fos-box').insertAdjacentHTML('beforeend',
        '<span data-automation-id=selectedItem>'+e.textContent+'</span>');e.parentElement.hidden=true;}
      </script>""")
    r = resolver(service, fallback="")
    r.resolve = AsyncMock(side_effect=AssertionError("no model guess for a major"))
    form = FormSession(page, r, tmp_path / "unused.pdf", lambda *a: None)
    field = next(f for f in (await form.scan())["fields"] if f["label"] == "Field of Study")
    field["group"] = "Education 1"
    with pytest.raises(ValueError):
        await form.fill(field, "Data Science")
    assert await page.locator("[data-automation-id=selectedItem]").count() == 0


async def test_a_catalog_without_either_major_never_lets_a_model_pick_from_its_first_page(page, service, tmp_path):
    from jobpilot.forms import FormSession

    # Jabil (Workday, 2026-10-23): neither search matched, the empty search listed the catalog alphabetically and a
    # model chose "Actuarial Science" for Data Science while trying the fallback major.
    await page.set_content("""<div data-automation-id="formField-fieldOfStudy"><label for=fos>Field of Study</label>
      <div data-automation-id=multiSelectContainer>
      <div data-automation-id=multiselectInputContainer id=fos-box>
        <input id=fos onkeydown="if(event.key==='Enter'){search(this.value);event.preventDefault()}">
      </div></div></div><div id=menu role=listbox hidden></div>
      <script>
      function search(q){const m=document.getElementById('menu');m.hidden=false;
        const items = !q.trim() ? ['Accounting','Actuarial Science','Advertising','Aerospace Engineering'] : ['No Items.'];
        m.innerHTML=items.map(t=>'<div role=option data-automation-id=promptOption onclick="pick(this)">'+t+'</div>').join('');}
      function pick(e){document.getElementById('fos-box').insertAdjacentHTML('beforeend',
        '<span data-automation-id=selectedItem>'+e.textContent+'</span>');e.parentElement.hidden=true;}
      document.addEventListener('keydown',e=>{if(e.key==='Escape')document.getElementById('menu').hidden=true});
      document.addEventListener('click',e=>{if(!e.target.closest('#menu,#fos-box'))document.getElementById('menu').hidden=true});
      </script>""")
    from jobpilot.schemas import Answer

    r = resolver(service)
    # The model's guess, as it was made in production.
    r.resolve = AsyncMock(return_value=[Answer(field_id="m1", value="Actuarial Science", disposition="answer")])
    form = FormSession(page, r, tmp_path / "unused.pdf", lambda *a: None)
    field = next(f for f in (await form.scan())["fields"] if f["label"] == "Field of Study")
    field["group"] = "Education 1"
    with pytest.raises(ValueError):
        await form.fill(field, "Data Science")
    assert await page.locator("[data-automation-id=selectedItem]").count() == 0


def test_a_bare_generic_word_is_not_the_recorded_major():
    from jobpilot.answers.history import major_option

    options = ["Actuarial Science", "Computer Science", "Data Processing", "Science"]
    assert major_option("Data Science", options) is None  # Ensemble's Workday catalog, 2026-10-28.
    assert major_option("Data Science", [*options, "Data Science"]) == "Data Science"
    assert major_option("Data Science", ["Data Science & Analytics", "Science"]) == "Data Science & Analytics"
