"""Replicas of three application pages, built from each site's real structure and scripts (read logged out, 2026-11-01). All names are invented."""

import json

from .scenarios import EMAIL_FIELD, NAME_FIELDS, POSTING, RESUME_FIELD, THANKS, form, page
from .site import Scenario

JOB_PAGE = page("Software Engineer", POSTING + '<p><a class="apply" href="/apply/1">Apply for this job</a></p>')

# ---- a month-range picker and pointer-transparent selects ----------------------------------------------------------------

_PICKER_SCRIPT = """<script>
const MONTHS=[...Array(12).keys()].map(i=>String(i+1).padStart(2,'0'));
const THIS_YEAR=new Date().getFullYear();
const startYears=[...Array(THIS_YEAR-1899).keys()].map(i=>String(THIS_YEAR-i));
const endYears=[...[...Array(5).keys()].map(i=>String(THIS_YEAR+5-i)),'Present',...startYears];
const P='atsx-date-picker-period-month';
function makePicker(name){
  const host=document.getElementById(name),state={start:{y:'',m:''},end:{y:'',m:''}},store=document.getElementById(name+'-v');
  const side=k=>host.querySelector('[data-side='+k+']');
  let popup=null,open_='';
  const hide=()=>{if(popup){popup.remove();popup=null}open_=''};
  function paint(k){const v=state[k],el=side(k);
    el.innerHTML=v.y==='Present'?'<span class="'+P+'-label-value '+P+'-label-toToday">Present</span>'
      :'<span class="'+(v.y?P+'-label-value ':'')+P+'-label-year">'+(v.y||'YYYY')+'</span><span class="'+P+'-label-separator">-</span><span class="'+(v.m?P+'-label-value ':'')+P+'-label-month">'+(v.m||'MM')+'</span>';
    store.value=[state.start.y&&state.start.y+'-'+state.start.m,state.end.y==='Present'?'Present':state.end.y&&state.end.y+'-'+state.end.m].filter(Boolean).join('|')}
  const done=v=>(v.y&&v.m)||v.y==='Present';
  function show(k){hide();open_=k;
    popup=document.createElement('div');popup.className='atsx-date-picker-dropdown';const r=host.getBoundingClientRect();
    popup.style.cssText='position:absolute;z-index:1050;background:#fff;border:1px solid #888;left:'+(r.left+scrollX)+'px;top:'+(r.bottom+scrollY+4)+'px';
    const panel=document.createElement('div');panel.className=P+'-panel';panel.style.cssText='display:flex;height:240px';popup.append(panel);
    for(const[kind,rows]of[['y',k==='start'?startYears:endYears],['m',MONTHS]]){
      const list=document.createElement('div');list.className=P+'-panel-list';list.style.cssText='overflow:auto;height:240px;width:90px';
      rows.forEach(t=>{const it=document.createElement('div');it.className=P+'-panel-list-item';it.textContent=t;it.setAttribute('data-cy',t);it.style.cursor='pointer';
        it.addEventListener('click',()=>{
          if(t==='Present'){state.end={y:'Present',m:''};paint('end');hide();return}
          state[k][kind]=t;paint(k);
          if(kind==='m'&&done(state[k])){ if(k==='start'&&!done(state.end))show('end');else hide() }
        });list.append(it)});
      panel.append(list)}
    document.body.append(popup)}
  host.querySelectorAll('[data-side]').forEach(el=>el.addEventListener('click',()=>{const k=el.dataset.side;open_===k?hide():show(k)}));
  document.addEventListener('mousedown',e=>{if(popup&&!popup.contains(e.target)&&!host.contains(e.target))hide()});
  paint('start');paint('end')}
function makeSelect(name,rows){
  const host=document.getElementById(name),sel=host.querySelector('.ud__select__selector'),input=host.querySelector('input'),ph=host.querySelector('.ud__select__selector__placeholder'),store=document.getElementById(name+'-v');
  let menu=null;const hide=()=>{if(menu){menu.remove();menu=null}sel.classList.remove('ud__select__selector-open','ud__select__selector-focus');input.setAttribute('aria-expanded','false')};
  sel.addEventListener('mousedown',e=>{e.preventDefault();if(menu){hide();return}
    sel.classList.add('ud__select__selector-open','ud__select__selector-focus');
    menu=document.createElement('div');menu.className='ud__select__dropdown';menu.id=name+'-list';menu.setAttribute('role','listbox');
    const r=sel.getBoundingClientRect();menu.style.cssText='position:absolute;z-index:1050;background:#fff;border:1px solid #888;width:'+r.width+'px;left:'+(r.left+scrollX)+'px;top:'+(r.bottom+scrollY)+'px';
    rows.forEach(t=>{const o=document.createElement('div');o.className='ud__select__list__item';o.setAttribute('role','option');o.textContent=t;
      o.addEventListener('click',()=>{ph.textContent=t;ph.classList.remove('empty');store.value=t;hide()});menu.append(o)});
    document.body.append(menu);input.setAttribute('aria-expanded','true')});
  document.addEventListener('mousedown',e=>{if(menu&&!menu.contains(e.target)&&!sel.contains(e.target))hide()})}
document.querySelectorAll('.period').forEach(el=>makePicker(el.id));
makeSelect('auth',['Yes','No']);makeSelect('visa',['Yes','No']);
</script>"""


def _period(name):
    return f"""<div class="field"><label class="q">Start &amp; end date <span style="color:#b00">*</span></label>
    <div class="atsx-form-item-control"><span class="atsx-form-item-children"><div id="{name}" class="period atsx-date-picker atsx-date-picker-period-month" style="display:flex;border:1px solid #888;height:34px;line-height:32px">
      <div data-side="start" class="atsx-date-picker-period-month-label" style="flex:1;cursor:pointer;padding-left:11px"></div>
      <div class="atsx-date-picker-period-line" style="width:1px;background:#ccc"></div>
      <div data-side="end" class="atsx-date-picker-period-month-label" style="flex:1;cursor:pointer;padding-left:11px"></div>
      <input class="atsx-date-picker-period-hidden-input" style="border:none;box-sizing:border-box;flex:0 1;height:0;min-width:0"></div></span></div>
    <input type="hidden" id="{name}-v" name="{name}"></div>"""


def _select(name, question):
    return f"""<div class="field"><div class="q">{question} <span style="color:#b00">*</span></div>
    <div id="{name}" class="ud__select" style="width:90%"><div class="ud__select__selector ud__select__selector-lg ud__select__selector-readOnly" style="display:flex;border:1px solid #888;height:36px;position:relative;cursor:pointer">
      <div class="ud__select__selector__content" style="flex:1;position:relative;overflow:hidden">
        <div class="ud__select__selector__placeholder empty" style="line-height:34px;padding-left:8px">Please select</div>
        <div class="ud__select__selector__search" style="position:absolute;top:0;left:0;width:100%;pointer-events:none">
          <input role="combobox" type="search" readonly aria-expanded="false" class="ud__select__selector__search__input ud__native-input" style="width:100%;margin:0;padding:0;border:0;background:transparent;height:34px;opacity:0;display:block"></div></div>
      <div class="ud__select__selector__arrow"><span class="universe-icon">v</span></div></div></div>
    <input type="hidden" id="{name}-v" name="{name}"></div>"""


_HISTORY_STYLE = "<style>.row{border:1px solid #ddd;padding:12px;margin:12px 0}.row h3{margin:0}.q{margin:14px 0 4px}</style>"

date_range = Scenario(
    "a_month_range_picker_and_selects_that_take_no_pointer",
    "TikTok (2026-11-01): one \"Start & end date\" picker per history row whose only input has no height, written through a panel of years and months, and two work-authorization "
    "menus whose input takes no pointer. Every write timed out \"hidden\" or \"covered\" and the practice run stopped on \"Start & end date\".",
    {"/jobs/1": JOB_PAGE, "/apply/1": page("Apply", _HISTORY_STYLE + "<h1>Apply: Software Engineer</h1>" + form(
        NAME_FIELDS, EMAIL_FIELD, RESUME_FIELD,
        '<h2>Work Experience</h2><div class="row"><h3>Work Experience 1</h3><label for="c1">Company name</label><input id="c1" name="company"><label for="t1">Title</label><input id="t1" name="title">', _period("work1"), "</div>",
        '<div class="row"><h3>Work Experience 2</h3><label for="c2">Company name</label><input id="c2" name="company2"><label for="t2">Title</label><input id="t2" name="title2">', _period("work2"), "</div>",
        '<h2>Education</h2><div class="row"><h3>Education 1</h3><label for="s1">School name</label><input id="s1" name="school">', _period("school1"), "</div>",
        _select("auth", "Are you legally authorized to work in the US without restriction?"),
        _select("visa", "Will you now or in the future require visa sponsorship or a visa transfer?"),
        '<p><button id="send" type="submit">Submit</button></p>', _PICKER_SCRIPT)), "/submit/1": THANKS},
    facts={"work_authorized_us": True, "requires_sponsorship": False},
    profile={"work": [{"company": "Example Labs", "title": "Engineer", "start": "2023-04", "current": True},
                      {"company": "Sample Works", "title": "Analyst", "start": "2020-09", "end": "2023-02"}],
             "education": [{"school": "Example University", "degree": "Bachelor's", "field_of_study": "Computer Science", "start": "2016-09", "end": "2020-06"}]},
    expect={"first_name": "Alex", "last_name": "Example", "email": "alex@example.test", "work1": "2023-04|Present", "work2": "2020-09|2023-02", "school1": "2016-09|2020-06",
            "auth": "Yes", "visa": "No", "company": "Example Labs", "school": "Example University"},
)

# ---- a web component's button outside its form ---------------------------------------------------------------------------

_PROXY_BUTTON = """<script>
class ExampleButton extends HTMLElement {
  static get observedAttributes(){return ['disabled']}
  connectedCallback(){
    if(!this.shadowRoot){const root=this.attachShadow({mode:'open'});root.innerHTML='<button part="b" style="padding:10px 22px;background:#174f47;color:#fff;border:0;border-radius:20px"><slot></slot></button>';
      root.querySelector('button').addEventListener('click',e=>{if(this.hasAttribute('disabled')){e.stopImmediatePropagation();return}this.proxy?.click()})}
    const f=document.getElementById(this.getAttribute('form'));
    if(f&&!this.proxy){this.proxy=document.createElement('button');this.proxy.type=this.getAttribute('type')||'button';this.proxy.value='undefined';this.proxy.style.display='none';f.append(this.proxy)}
    this.paint()}
  attributeChangedCallback(){this.paint()}
  paint(){const b=this.shadowRoot?.querySelector('button');if(b){b.disabled=this.hasAttribute('disabled');b.style.background=this.hasAttribute('disabled')?'#ccc':'#174f47'}}
}
customElements.define('example-button',ExampleButton);
const check=()=>{const ok=['first','last','phone'].every(n=>document.getElementById(n).value.trim());const b=document.getElementById('create');ok?b.removeAttribute('disabled'):b.setAttribute('disabled','')};
document.querySelectorAll('#details input').forEach(i=>{i.addEventListener('input',check);i.addEventListener('blur',check)});
document.getElementById('details').addEventListener('submit',async e=>{e.preventDefault();
  const r=await fetch('/account/details',{method:'POST',body:new URLSearchParams(new FormData(e.target))});
  if(r.ok)location.href='/apply/1'});
check();
</script>"""

web_component_button = Scenario(
    "a_create_account_button_outside_its_form",
    "UKG (2026-11-01): \"Almost there!\" takes a name and phone, and its Create account is a web component outside the form, which clicks a hidden button inside it. The "
    "engine's own submit guard cancelled that submit, so the click did nothing on six runs, and nothing said why.",
    {"/jobs/1": page("Software Engineer", POSTING + '<p><a class="apply" href="/account/details">Apply for this job</a></p>'),
     "/account/details": lambda request: (302, "/apply/1") if request.method == "POST" else page(
         "Almost there", """<h2>Almost there!</h2><p>Please provide your name to set up your account</p><form method="POST" id="details" novalidate>
         <label for="first">First name *</label><input id="first" name="first" required><label for="last">Last name *</label><input id="last" name="last" required>
         <label for="phone">Phone number *</label><input id="phone" name="phone" type="tel" required>
         <label><input type="checkbox" name="sms" style="display:inline;width:auto"> I want to receive recruiting-related SMS messages and by checking this box I agree to the terms</label></form>
         <example-button id="create" type="submit" form="details" disabled> &nbsp; Create account</example-button>""" + _PROXY_BUTTON),
     "/apply/1": page("Apply", "<h1>Apply: Software Engineer</h1>" + form(NAME_FIELDS, EMAIL_FIELD, RESUME_FIELD, '<p><button id="send" type="submit">Submit application</button></p>')),
     "/submit/1": THANKS},
    facts={"phone": "2025550100"},
    profile={"phone": "2025550100"},
    terms=True,
)

# ---- a page that commits empty and draws itself a moment later ------------------------------------------------------------

slow_blank = Scenario(
    "a_step_that_is_blank_for_seconds_after_next",
    "SuccessFactors and five other platforms (16 runs since 10-12): a redirect commits an empty page that draws itself seconds later, and the run stopped after about five seconds "
    "with \"Browser observation stopped responding\", though every answer was filled and verified.",
    {"/jobs/1": JOB_PAGE,
     "/apply/1": page("Apply", "<h1>Step 1</h1>" + form(NAME_FIELDS, EMAIL_FIELD, '<p><button type="submit">Next</button></p>', action="/apply/2")),
     "/apply/2": lambda request: """<!doctype html><html lang="en"><meta charset="utf-8"><title></title><body><script>
       const q=""" + json.dumps(request.form) + """;setTimeout(()=>{document.title='Apply';document.body.innerHTML=`<h1>Step 2</h1><form method="post" action="/submit/1" enctype="multipart/form-data">
       <input type="hidden" name="first_name" value="${q.first_name}"><input type="hidden" name="last_name" value="${q.last_name}"><input type="hidden" name="email" value="${q.email}">
       <label for="r">Resume</label><input id="r" name="resume" type="file" required accept=".pdf"><p><button id="send" type="submit">Submit application</button></p></form>`},9000);</script></body></html>""",
     "/submit/1": THANKS},
)

SCENARIOS = [date_range, web_component_button, slow_blank]
