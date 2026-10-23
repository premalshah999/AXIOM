"""Oracle re-renders its job page after the first scan; the Apply click raised "Control is stale" and the run waited
for a backoff retry (Verisk, Dallas County 2026-10-23). The Apply control is found again and clicked once."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

from jobpilot.adapters.base import Adapter


async def test_a_stale_apply_control_is_found_again():
    value = Adapter.__new__(Adapter)
    value.attempts = __import__("collections").Counter()
    first, second = {"id": "a1", "label": "Apply Now"}, {"id": "a2", "label": "Apply Now"}
    value.find_apply = lambda obs: obs["button"]
    form = SimpleNamespace(scan=AsyncMock(return_value={"button": second}),
                           page=SimpleNamespace(url="https://acme.fa.oraclecloud.com/job/1/apply"))
    value.e = SimpleNamespace(form=form)
    value.click = AsyncMock(side_effect=[ValueError("Control is stale; inspect again"), None])
    assert await value.on_job_details({"button": first}) is None
    assert [c.args[0]["id"] for c in value.click.await_args_list] == ["a1", "a2"]
