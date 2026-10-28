"""Shadow-root questionnaire dialogs and option captions; synthetic applicant data."""

import pytest

from jobpilot.answers import Resolver
from jobpilot.forms import FormSession
from jobpilot.schemas import Profile


PAGE = """<main id=main aria-hidden=true>
<span id=q>Do you have permission to work?</span><div role=radiogroup aria-labelledby=q>
<example-choice role=radio aria-checked=false data-caption=Yes></example-choice>
<example-choice role=radio aria-checked=false data-caption=No></example-choice>
</div></main>
<example-tip id=tip><p>{message}</p></example-tip>
<script>
customElements.define('example-choice', class extends HTMLElement {{
 connectedCallback() {{
  this.attachShadow({{mode:'open'}}).innerHTML='<span>'+this.dataset.caption+'</span>';
  this.onclick=()=>{{
   this.parentElement.querySelectorAll('[role=radio]').forEach(r=>r.setAttribute('aria-checked',String(r===this)));
  }};
 }}
}});
customElements.define('example-tip', class extends HTMLElement {{
 connectedCallback() {{
  const root=this.attachShadow({{mode:'open'}});
  root.innerHTML='<section role=dialog aria-modal=true style="width:400px"><h2>Quick tip</h2><slot></slot><button>Okay</button></section>';
  root.querySelector('button').onclick=()=>{{this.remove();document.querySelector('main').removeAttribute('aria-hidden');}};
 }}
}});
</script>"""


@pytest.mark.parametrize('message, dismissed', [
    ('Choose the answer that applies to you.', True),
    ('Are you sure you want to withdraw your application?', False),
])
async def test_shadow_dialog_reads_slotted_warning_before_acknowledging(page, tmp_path, message, dismissed):
    await page.set_content(PAGE.format(message=message))
    form = FormSession(page, Resolver(Profile(), None, None), tmp_path / 'r.pdf', lambda *a, **k: None)
    obs = await form.scan()
    assert (await page.locator('#tip').count() == 0) is dismissed
    if not dismissed:
        assert not obs['fields']
        return
    field = next(f for f in obs['fields'] if f['type'] == 'radiogroup')
    assert field['options'] == ['Yes', 'No']
    await form.fill(field, 'No')
    obs = await form.scan()
    assert next(f for f in obs['fields'] if f['type'] == 'radiogroup')['value'] == 'No'
