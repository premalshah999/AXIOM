"""ADP, 2026-10-29: a navigator choose refused as stale, because the menu it had read was closed, was retried for 30
decisions. All pages here are synthetic."""

from jobpilot.answers import Resolver
from jobpilot.forms import FormSession
from jobpilot.schemas import Answer, Profile

PROFILE = {
    "email": "alexample.apply@gmail.com",
    "linkedin": "https://www.linkedin.com/in/alex-example",
    "github": "https://github.com/alexexample",
}


def session(page, service, tmp_path, employer="ExampleSoft Inc", **profile):
    resolver = Resolver(
        Profile(**{**PROFILE, **profile}), service.db, service.config, employer=employer,
        job={"ats": "custom", "url": "https://x.test"},
    )
    return FormSession(page, resolver, tmp_path / "unused.pdf", lambda *a: None)


CLOSING_MENU = """<label for=s>Social Media Source</label>
<input id=s role=combobox aria-controls=menu aria-expanded=false
  onclick="this.setAttribute('aria-expanded','true');document.querySelector('#menu').hidden=false">
<div id=menu role=listbox hidden>
  <div role=option onclick="const i=document.querySelector('#s');i.value=this.textContent;
    i.setAttribute('aria-expanded','false');document.querySelector('#menu').hidden=true">LinkedIn</div>
  <div role=option onclick="const i=document.querySelector('#s');i.value=this.textContent;
    i.setAttribute('aria-expanded','false');document.querySelector('#menu').hidden=true">Facebook</div>
</div>"""


async def test_a_choice_read_from_a_menu_that_has_since_closed_reopens_the_menu(page, service, tmp_path):
    from jobpilot import widgets

    await page.set_content(CLOSING_MENU)
    form = session(page, service, tmp_path, application_source="LinkedIn")
    obs = await form.scan()
    field = next(f for f in obs["fields"] if f["label"] == "Social Media Source")
    await form.control_action("open", field["id"], obs["revision"], owner_id=obs["owner_id"])
    options = await widgets.visible_options(page.main_frame, "menu")
    linkedin = next(o for o in options if o["text"] == "LinkedIn")
    # The scan between the model's read and its choose closed the menu.
    await page.evaluate("() => { const m = document.querySelector('#menu'); m.hidden = true; "
                        "document.querySelector('#s').setAttribute('aria-expanded', 'false') }")
    obs = await form.scan()
    chosen = await form.control_action(
        "choose", field["id"], obs["revision"], owner_id=obs["owner_id"], option_id=linkedin["id"]
    )
    assert chosen.executed and not chosen.failure_category, chosen
    assert await page.locator("#s").input_value() == "LinkedIn"


async def test_a_choice_no_menu_holds_says_what_to_do_instead(page, service, tmp_path):
    await page.set_content(CLOSING_MENU)
    form = session(page, service, tmp_path, application_source="LinkedIn")
    obs = await form.scan()
    field = next(f for f in obs["fields"] if f["label"] == "Social Media Source")
    refused = await form.control_action(
        "choose", field["id"], obs["revision"], owner_id=obs["owner_id"], option_id="gone"
    )
    assert not refused.executed and refused.failure_category == "unavailable"
    assert "inspect_options" in refused.detail and "Choice is stale or outside this control" in refused.detail


# ADP's sdf-select lists its items in the host's own children and commits through its own driver; the open pane is not
# its catalog. Midco's start-availability question (2026-10-30) was refused as stale on 31 runs of one job.
def sdf_page(label):
    return f"""<div class=field><label id=lab>{label}</label>
<sdf-select-simple id=q aria-labelledby=lab required>
  <sdf-select-item label="-" value=""></sdf-select-item>
  <sdf-select-item label="Indeed" value="indeed"></sdf-select-item>
  <sdf-select-item label="LinkedIn" value="linkedin"></sdf-select-item>
  <sdf-select-item label="Immediately" value="now"></sdf-select-item>
</sdf-select-simple></div>
<script>
customElements.define('sdf-select-item', class extends HTMLElement {{
  connectedCallback() {{ this.style.display = 'block'; this.textContent = this.getAttribute('label'); }}
}});
customElements.define('sdf-select-simple', class extends HTMLElement {{
  constructor() {{ super(); this.value = ''; this.open = false;
    const root = this.attachShadow({{mode: 'open'}});
    root.innerHTML = '<div class=shown>Select</div><div role=listbox hidden><slot></slot></div>';
    this.addEventListener('click', e => {{
      const item = e.target.closest && e.target.closest('sdf-select-item');
      if (item && this.open) {{
        this.querySelectorAll('sdf-select-item').forEach(i => i.removeAttribute('selected'));
        item.setAttribute('selected', ''); this.value = item.getAttribute('value');
        root.querySelector('.shown').textContent = item.getAttribute('label');
        this.open = false; root.querySelector('[role=listbox]').hidden = true; return;
      }}
      this.open = true; root.querySelector('[role=listbox]').hidden = false;
    }});
  }}
}});
</script>"""


async def test_a_stale_choice_on_an_sdf_select_commits_the_option_the_evidence_supports(page, service, tmp_path):
    await page.set_content(sdf_page("How did you hear about this job?"))
    form = session(page, service, tmp_path, application_source="LinkedIn")
    obs = await form.scan()
    field = next(f for f in obs["fields"] if f.get("widget") == "sdf-select")
    done = await form.control_action(
        "choose", field["id"], obs["revision"], owner_id=obs["owner_id"], option_id="gone"
    )
    assert done.executed and not done.failure_category, done
    assert await page.evaluate("document.getElementById('q').value") == "linkedin"


async def test_an_sdf_select_with_no_supported_answer_asks_instead_of_going_stale(page, service, tmp_path):
    await page.set_content(sdf_page("If offered the position, how soon would you be available to start?"))
    form = session(page, service, tmp_path)
    obs = await form.scan()
    field = next(f for f in obs["fields"] if f.get("widget") == "sdf-select")

    async def unknown(fields):
        return [Answer(field_id=f["id"], disposition="review", reason="Not stated in the profile",
                       blocker_type="missing_fact") for f in fields]

    form.resolver.resolve = unknown
    refused = await form.control_action(
        "choose", field["id"], obs["revision"], owner_id=obs["owner_id"], option_id="gone"
    )
    assert not refused.executed and refused.failure_category == "missing_fact", refused
    assert "stale" not in refused.detail
    assert any(q["question"].startswith("If offered the position") for q in form.pending)
