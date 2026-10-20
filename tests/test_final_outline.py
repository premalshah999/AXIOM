"""The final page outline kept after an unproven commit (Qualcomm 2026-10-17): structure and visible text only."""

from jobpilot.browser import FINAL_OUTLINE


async def test_outline_keeps_structure_and_text_but_no_input_values(page):
    await page.set_content("""<div class="success"><h4 id="msg">Thank you for your application, Alex!</h4>
      <input value="secret-typed-value"><script>var token = "abc"</script><p role="status">Track your application</p></div>""")
    outline = await page.evaluate(FINAL_OUTLINE)
    assert "h4#msg: Thank you for your application, Alex!" in outline
    assert "p[status]: Track your application" in outline
    assert "secret-typed-value" not in outline and "token" not in outline
