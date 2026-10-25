"""Marriott's Oracle tenant asks for its code in six one-digit boxes (2026-10-25). The engine filled every box, the
digits showed on screen, but the component keeps its own model from key events: it registered nothing, Verify did
nothing, and a correct code was reported as refused. Split boxes are typed digit by digit."""

from jobpilot.adapters.base import enter_code

PAGE = """<div id=boxes>%s</div><button id=verify>Verify</button><p id=result></p>
<script>
const model = Array(6).fill('');
document.querySelectorAll('#boxes input').forEach((box, i) => {
  box.addEventListener('keydown', ev => { if (/^[0-9]$/.test(ev.key)) model[i] = ev.key; });
});
document.getElementById('verify').onclick = () => {
  document.getElementById('result').textContent = model.join('') === '973702' ? 'verified' : 'refused';
};
</script>""" % "".join(f'<input maxlength=1 aria-label="Enter verification code digit {i} of six.">' for i in range(1, 7))


async def test_a_split_code_is_typed_so_the_component_registers_every_digit(page):
    await page.set_content(PAGE)
    boxes = [page.locator("#boxes input").nth(i) for i in range(6)]
    await enter_code(boxes, "973702")
    await page.click("#verify")
    assert await page.text_content("#result") == "verified"


async def test_a_single_code_field_is_filled_whole(page):
    await page.set_content('<input id=code maxlength=6>')
    await enter_code([page.locator("#code")], "973702")
    assert await page.input_value("#code") == "973702"
