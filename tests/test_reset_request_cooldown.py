import time
from unittest.mock import AsyncMock

import pytest

from jobpilot.adapters.base import RESET_RETRY_SECONDS
from test_one_page_reset import RESET, SITE, adapter


@pytest.mark.parametrize("age,blocked", [(600, True), (3600, True), (RESET_RETRY_SECONDS + 10, False)])
async def test_reopened_reset_page_obeys_account_cooldown(service, age, blocked):
    value = adapter(service)
    value.accounts.mark(SITE + "/reset-password", "reset_requested", reset_requested_at=time.time() - age)
    value.reset_outcome = AsyncMock(return_value=("refused", "refused"))
    await value.request_reset(RESET, "reopened")
    assert value.click.await_count == (0 if blocked else 1)
    assert value.arm_mail.await_count == (0 if blocked else 1)


async def test_a_sibling_reset_while_waiting_for_mail_lease_is_not_repeated(service):
    value = adapter(service)
    value.release_mail = lambda: value.finished.append("released")

    async def arm(*_args):
        value.accounts.mark(SITE + "/reset-password", "reset_requested", reset_requested_at=time.time())
        return True

    value.arm_mail = AsyncMock(side_effect=arm)
    result = await value.request_reset(RESET, "reopened")
    assert result["state"] == "waiting_browser"
    value.click.assert_not_awaited()
    assert value.finished == ["released", "released"]
