import pytest

from jobpilot.answers import Resolver
from jobpilot.forms import FormSession
from jobpilot.schemas import Profile


@pytest.mark.parametrize("host,expected_type", [("careers-example.icims.com", "number"), ("example.test", "text")])
async def test_hamilton_numeric_widget_is_not_a_prose_question(page, tmp_path, host, expected_type):
    """Hamilton, run 495c533051ae4ff492598936213d1400, 2026-10-21: Q1768 posted 679 characters instead of digits."""
    url = f"https://{host}/jobs/1/engineer/questions"
    html = ('<label for="Q1768">How many years of experience do you have testing robotic platforms?</label>'
            '<input id="Q1768" name="Q1768" type="text" class="iCIMS_Forms_NumberField" required>')
    await page.route(url, lambda route: route.fulfill(body=html, content_type="text/html"))
    await page.goto(url)
    resolver = Resolver(Profile(), None, None)
    form = FormSession(page, resolver, tmp_path / "resume.pdf", lambda *a: None)
    field = (await form.scan())["fields"][0]
    assert field["type"] == expected_type
    if expected_type == "number":
        assert resolver._ans(field, "I have worked on several related projects", ["fact:experience"]) is None
        answer = resolver._ans(field, "2", ["fact:experience"])
        assert answer.value == "2"
        await form.fill(field, answer.value)
        assert await page.locator('#Q1768').input_value() == "2"
