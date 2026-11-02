"""A replica of one career site's application page, built from its real structure (read logged out, 2026-11-01): no <form>, role=button
divs for Submit, a recruiting chat column with its own "Sign up", a searched "Current location" picker on a button, and an optional
account section. The page marks nothing required and checks everything itself when Submit is pressed. All names are invented."""

import json

from .scenarios import POSTING, THANKS, page
from .site import Scenario

CITIES = ["Exampleton Hills, IL", "Exampleton, TN", "Exampleton, MD", "Exampleton, WI", "Exampleton, QLD, Australia", "Exampleton, PA"]
FIRST_PAGE = ["Fairview, CA", "New Harbor, NY", "Lakeside, WA", "Sunnydale, CA", "Aiken Ridge, SC", "Altona, IA", "Ashgrove, VA"]


def typeahead(request):
    """The site's own city search: an empty search lists its offices, a typed one lists places by name."""
    wanted = str(request.query.get("q", [""])[0]).lower()
    return json.dumps([c for c in [*FIRST_PAGE, *CITIES] if wanted in c.lower()] if wanted else FIRST_PAGE)


def _radios(name, label, rows):
    inputs = "".join(f'<div><label><div><input type="radio" name="{name}" value="{value}"></div><div><div>{text}</div></div></label></div>' for value, text in rows)
    return f'<div aria-labelledby="{name}-l" role="radiogroup"><div><span id="{name}-l">{label}</span></div>{inputs}</div>'


def _picker(name, label, shown=""):
    return f"""<div id="wrap-{name}" style="position:relative"><label for="{name}-b">{label}</label><div>
    <button aria-controls="{name}-pop" aria-expanded="false" aria-invalid="false" aria-label="{label}" id="{name}-b" role="combobox" type="button" style="display:block;width:90%;height:40px;background:#fff;color:#222;border:1px solid #888"><div>{shown}</div></button></div></div>"""


FORM = f"""<h1>Software Engineer</h1>
<div><h2>Resume upload</h2><span>Acceptable formats are .docx or .pdf.</span>
  <div><div role="button" tabindex="0" id="up" onclick="document.getElementById('file').click()">Upload resume</div>
  <input id="file" accept="application/pdf" type="file" style="display:none"></div></div>
<div><span>Please select one or more locations where you'd like to apply.</span><div role="group"><div role="list">
  {"".join(f'<div role="listitem"><div><label><div><input class="pref" aria-checked="false" aria-invalid="false" type="checkbox" value="false" data-place="{p}"></div><div><div><span>{p}</span></div></div></label></div></div>' for p in ["Lakeside, WA", "Fairview, CA", "New Harbor, NY", "Sunnydale, CA"])}
</div></div></div>
<div><label><div><div>First name</div><div><input id="first" autocomplete="on" type="text"></div></div></label></div>
<div><label><div><div>Last name</div><div><input id="last" autocomplete="on" type="text"></div></div></label></div>
{_picker("city", "Current location")}
<div><label><div><div>Email</div><div><input id="email" autocomplete="on" type="text"></div></div></label></div>
<div><span>Phone number</span>{_picker("code", "Code", "+1")}
  <label><div><div>Phone number</div><div><input id="phone" inputmode="numeric" autocomplete="on" type="text"></div></div></label></div>
<div><label><div><div>Website (Examples: LinkedIn, GitHub, portfolio)</div><div><input id="site" autocomplete="on" type="text"></div></div></label></div>
<hr aria-hidden="true"><h2>Self ID</h2>
{_radios("gender", "Indicate your gender:", [("male", "Male"), ("female", "Female"), ("decline", "I choose not to disclose")])}
{_radios("race", "Indicate your race and ethnicity:", [("a", "Asian"), ("b", "White"), ("two", "Two or More Races"), ("decline", "I choose not to disclose")])}
{_radios("veteran", "Indicate your protected veteran status:", [("p", "I identify as one or more of the classifications of protected veteran listed."), ("o", "Other veteran"), ("n", "I am not a protected veteran"), ("decline", "I choose not to disclose")])}
<hr aria-hidden="true"><div><h2>Create a Career Profile account (optional)</h2>
<span>With an account, you can track your applications. The system will create your account after you submit your application.</span>
<div aria-label="Select an option" role="radiogroup"><div aria-checked="true" role="radio" tabindex="0"><div>Use a password</div></div>
<div aria-checked="false" role="radio" tabindex="-1"><div>Use a one-time code</div></div>
<div aria-checked="false" role="radio" tabindex="-1"><div>Already have an account</div></div></div></div>
<div><label><div><div>Password</div><div><input id="pw" autocomplete="on" type="password"></div></div></label></div>
<div><label><div><div>Confirm password</div><div><input id="pw2" autocomplete="on" type="password"></div></div></label></div>
<div id="cards"></div>
<div style="position:sticky;bottom:0;background:#fff;padding:8px"><div role="button" tabindex="0" id="cancel">Cancel</div><span id="status"></span>
  <div aria-busy="false" role="button" tabindex="0" id="send" style="display:inline-block;padding:10px 22px;background:#174f47;color:#fff"><div><span>Submit</span></div></div></div>
<aside style="position:fixed;right:8px;bottom:8px;width:240px;background:#fff;border:1px solid #888;padding:8px">
  <div aria-label="Minimize chat" role="button" tabindex="0">-</div><span>Recruiters can view your conversations with AI.</span>
  <div aria-label="Upload resume to get job recommendations" role="button" tabindex="0">+</div>
  <div><div role="button" tabindex="0" id="chat-signup"><div><span>Sign up</span></div></div><span>to ask follow-up questions</span></div></aside>
<script>
const $=id=>document.getElementById(id), chosen={{city:'',code:'+1'}};
function closePop(name){{const b=$(name+'-b'),p=$(name+'-pop');if(p)p.remove();b.setAttribute('aria-expanded','false')}}
async function rows(name,q){{const list=$(name+'-list');if(!list)return;
  const got=name==='city'?await (await fetch('/typeahead?q='+encodeURIComponent(q),{{method:'POST'}})).json():['+1','+44','+91'];
  if(!$(name+'-list'))return;list.innerHTML='';
  got.forEach((t,k)=>{{const li=document.createElement('li');li.setAttribute('role','option');li.setAttribute('aria-selected','false');li.id=name+'-item-'+k;li.innerHTML='<div><div>'+t+'</div></div>';
    li.addEventListener('click',()=>{{chosen[name]=t;$(name+'-b').querySelector('div').textContent=t;$(name+'-b').setAttribute('aria-invalid','false');closePop(name)}});list.append(li)}})}}
for(const name of ['city','code']){{const b=$(name+'-b');
  b.addEventListener('click',()=>{{if($(name+'-pop')){{closePop(name);return}}
    const p=document.createElement('div');p.id=name+'-pop';p.style.cssText='position:absolute;left:0;right:0;background:#fff;border:1px solid #888;z-index:50;max-height:280px;overflow:auto';
    p.innerHTML='<div><input aria-autocomplete="list" aria-controls="'+name+'-list" aria-label="Search" autocomplete="off" placeholder="Search" type="text"></div><div><ul id="'+name+'-list" role="listbox" style="list-style:none;margin:0;padding:0"></ul></div>';
    $('wrap-'+name).append(p);b.setAttribute('aria-expanded','true');const i=p.querySelector('input');i.focus();rows(name,'');
    i.addEventListener('input',()=>rows(name,i.value));i.addEventListener('keydown',e=>{{if(e.key==='Escape')closePop(name)}})}})}}
document.addEventListener('mousedown',e=>{{for(const name of ['city','code']){{const p=$(name+'-pop');if(p&&!p.contains(e.target)&&!$(name+'-b').contains(e.target))closePop(name)}}}});
document.querySelectorAll('.pref').forEach(c=>c.addEventListener('change',()=>{{c.value=String(c.checked);c.setAttribute('aria-checked',String(c.checked))}}));
const signUp=()=>{{fetch('/signup-dialog',{{method:'POST'}});
  const d=document.createElement('div');d.id='dialog';d.setAttribute('role','dialog');d.setAttribute('aria-modal','true');d.setAttribute('aria-label','Sign up for Career Profile');
  d.style.cssText='position:fixed;inset:40px;background:#fff;border:1px solid #888;z-index:99';
  d.innerHTML='<h2>Sign up for Career Profile</h2><div aria-label="Close Sign up for Career Profile dialog" role="button" tabindex="0" id="dialog-x">x</div><label>First name<input></label><label>Last name<input></label><label>Email<input type="email"></label><div role="button" tabindex="0">Sign up with a one-time code</div><div role="button" tabindex="0">Sign up with a password</div><div role="button" tabindex="0">Log in</div>';
  document.body.append(d);$('dialog-x').addEventListener('click',()=>d.remove())}};
$('chat-signup').addEventListener('click',signUp);
function refuse(messages){{$('cards').innerHTML=messages.map(m=>'<div role="alert">'+m+'</div>').join('');$('status').textContent='Please complete the missing fields'}}
$('send').addEventListener('click',async()=>{{
  const prefs=[...document.querySelectorAll('.pref')].filter(c=>c.checked).map(c=>c.dataset.place),errors=[];
  const radio=n=>(document.querySelector('input[name='+n+']:checked')||{{}}).value||'';
  if(!$('first').value)errors.push('First name is required.');if(!$('last').value)errors.push('Last name is required.');
  if(!chosen.city){{errors.push('Current location is required.');$('city-b').setAttribute('aria-invalid','true')}}
  if(!prefs.length)errors.push('Location preferences must have at least one selected.');
  if(!/.+@.+/.test($('email').value))errors.push('Email must be valid.');if(!$('phone').value)errors.push('Phone Number is required.');
  if(!$('file').files.length)errors.push('Resume is required.');
  for(const[n,t]of[['gender','Gender identification'],['race','Race and ethnicity identification'],['veteran','Veteran status']])if(!radio(n))errors.push(t+' is required.');
  if($('pw').value!==$('pw2').value)errors.push('Confirm password does not match.');
  if(errors.length)return refuse(errors);
  const body=new FormData();body.append('first_name',$('first').value);body.append('last_name',$('last').value);body.append('email',$('email').value);body.append('phone',$('phone').value);
  body.append('city',chosen.city);body.append('places',prefs.join('|'));body.append('gender',radio('gender'));body.append('race',radio('race'));body.append('veteran',radio('veteran'));
  body.append('password',$('pw').value);body.append('resume',$('file').files[0]);
  await fetch('/submit/1',{{method:'POST',body}});location.href='/thanks'}});
</script>"""

_ADDRESS = {"address": {"line1": "1 Example Way", "city": "Exampleton", "state": "MD", "postal_code": "20000", "country": "United States of America"},
            "location": "Exampleton, MD", "phone": "+1 555 010 0100"}

career_profile = Scenario(
    "a_career_profile_page_with_a_chat_and_an_optional_account",
    "Meta (2026-11-01): the city search listed \"Exampleton, MD\" and read as no match; Submit was refused; recovery took the optional passwords for a sign-up page and pressed "
    "the chat's Sign up; its dialog hid the form's Submit from every later scan and its \"Close ... dialog\" was refused. \"No final submit control is on this page\".",
    {"/jobs/1": page("Software Engineer", POSTING + '<p><a class="apply" href="/apply/1">Apply for this job</a></p>'), "/apply/1": page("Apply", FORM),
     "/typeahead": typeahead, "/signup-dialog": "{}", "/submit/1": "{}", "/thanks": THANKS},
    location="Fairview, CA",
    never=("/signup-dialog",),
    expect={"first_name": "Alex", "last_name": "Example", "email": "alex@example.test", "city": "Exampleton, MD", "places": "Fairview, CA", "password": "",
            "gender": "decline", "race": "decline", "veteran": "decline"},
    profile=_ADDRESS,
)

SCENARIOS = [career_profile]
