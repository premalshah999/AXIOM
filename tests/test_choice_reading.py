"""PASS/FAIL for questions that reach the owner with their choices (2026-11-01), through the real engine and a real Chrome.

The employer's page has three pickers no saved fact answers:
- a menu that fetches its rows three seconds after it opens and shows them 15 at a time as it is scrolled (90 offices);
- a short menu that renders at once;
- a search box that lists nothing until something is typed.
Before: the scan read each menu once, about a second after opening it, so the first came out empty or as its first screen.
After: the owner is asked with the complete list of the first two, and the third is marked as a search to type into."""

import pytest
from sqlalchemy import select

from jobpilot.db import Review

from test_take_over import World, no_saved_fact, until  # noqa: F401  (the fixture applies to this module too)

pytestmark = [pytest.mark.browser, pytest.mark.integration]

PAGE = """<!doctype html><html><head><title>Apply: Analyst</title><style>
ul[role=listbox]{border:1px solid #888;max-height:150px;overflow-y:auto;margin:0;padding:0;list-style:none;width:260px}
li[role=option]{padding:4px 8px;height:24px}
</style></head><body><h1>Analyst</h1>
<form method=post action="/applications" enctype="multipart/form-data">
<label for=f>First Name</label><input id=f name=first_name required>
<label for=l>Last Name</label><input id=l name=last_name required>
<label for=m>Email</label><input id=m name=email type=email required>
<label for=o>Which office would you work from?</label>
<input id=o role=combobox aria-expanded=false aria-controls=ol aria-haspopup=listbox readonly placeholder="Select..." required>
<ul id=ol role=listbox hidden></ul>
<label for=t>How will you get to work?</label>
<input id=t role=combobox aria-expanded=false aria-controls=tl aria-haspopup=listbox readonly placeholder="Select..." required>
<ul id=tl role=listbox hidden><li role=option>Car</li><li role=option>Train</li><li role=option>Bike</li></ul>
<label for=c>Which city do you live in?</label>
<input id=c role=combobox aria-autocomplete=list aria-expanded=false aria-controls=cl aria-haspopup=listbox autocomplete=off required>
<ul id=cl role=listbox hidden></ul>
<label for=r>Resume</label><input id=r name=resume type=file required>
<button type=submit>Submit Application</button></form>
<script>
const ol=document.getElementById('ol'), office=document.getElementById('o');
let loaded=false, shown=0; const TOTAL=90;
function more(){ for(let i=0;i<15&&shown<TOTAL;i++){ const li=document.createElement('li'); li.setAttribute('role','option'); li.textContent='Office '+(++shown); ol.appendChild(li);} }
office.addEventListener('click',()=>{ ol.hidden=false; office.setAttribute('aria-expanded','true');
  if(!loaded){ loaded=true; setTimeout(more,3000);} });
ol.addEventListener('scroll',()=>{ if(ol.scrollTop+ol.clientHeight>=ol.scrollHeight-20) more(); });
const tl=document.getElementById('tl'), t=document.getElementById('t');
t.addEventListener('click',()=>{ tl.hidden=false; t.setAttribute('aria-expanded','true'); });
const cl=document.getElementById('cl'), c=document.getElementById('c');
c.addEventListener('focus',()=>{ cl.hidden=false; c.setAttribute('aria-expanded','true'); });
c.addEventListener('click',()=>{ cl.hidden=false; c.setAttribute('aria-expanded','true'); });
c.addEventListener('input',()=>{ cl.innerHTML=''; if(c.value.length<2) return; for(const n of ['Springfield','Shelbyville','Capital City']) if(n.toLowerCase().includes(c.value.toLowerCase())){ const li=document.createElement('li'); li.setAttribute('role','option'); li.textContent=n; cl.appendChild(li);} });
// A menu closes on Escape and on a click elsewhere, as every real one does.
function closeAll(except){ for(const [list,input] of [[ol,office],[tl,t],[cl,c]]) if(list!==except){ list.hidden=true; input.setAttribute('aria-expanded','false'); } }
document.addEventListener('keydown',e=>{ if(e.key==='Escape') closeAll(null); });
document.addEventListener('click',e=>{ if(!e.target.closest('ul[role=listbox],input[role=combobox]')) closeAll(null); });
for(const [list,input] of [[ol,office],[tl,t],[cl,c]]) input.addEventListener('click',()=>closeAll(list));
</script></body></html>"""


async def test_a_question_reaches_the_owner_with_every_choice_the_page_offers(service, monkeypatch):
    import test_take_over

    monkeypatch.setattr(test_take_over, "FORM", PAGE)
    world = await World(service).setup()
    try:
        await world.stop_at_question()
        with service.db.session() as s:
            asked = {r.question: r for r in s.scalars(select(Review).where(Review.answer.is_(None)))}
        def find(word):
            found = next((r for q, r in asked.items() if word in q.lower()), None)
            assert found is not None, (word, list(asked), world.runs(world.job_id), world.trail(12))
            return found

        office, transport, city = find("office"), find("get to work"), find("city")
        # The lazily loaded, scrolled menu: all 90 rows, in order, and known to be the whole list.
        assert office.options == [f"Office {i}" for i in range(1, 91)], (len(office.options), office.options[:20])
        assert not (office.context or {}).get("options_partial")
        assert transport.options == ["Car", "Train", "Bike"]
        # The search box lists nothing until typed: asked as a search, never as an empty dropdown.
        assert city.options == [] and (city.context or {}).get("search_only"), city.context
        from jobpilot.owner_questions import present, requirement

        shown = present({"question": city.question, "options": city.options, "context": city.context, "reason": city.reason, "key": city.key})
        assert shown["kind"] == "text" and "type the start" in shown["requirement"].lower(), shown["requirement"]
    finally:
        await world.close()
