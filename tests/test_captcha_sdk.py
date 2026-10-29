import json
import asyncio
from unittest.mock import AsyncMock

import pytest

from jobpilot import captcha_client
from jobpilot.captcha import CaptchaSolver, DETECT, HOOK


@pytest.mark.parametrize("kind", ["recaptcha", "hcaptcha", "turnstile"])
def test_official_sdk_uses_own_method_and_one_purchase(kind, monkeypatch):
    calls = []

    # Use the actual SDK method/serialization, fake only its API boundary.
    def send(self, **kwargs):
        calls.append(kwargs)
        return "OK|synthetic-task"

    monkeypatch.setattr("twocaptcha.api.ApiClient.in_", send)

    def wait_result(self, *args):
        assert self.default_timeout == self.recaptcha_timeout == 300
        assert self.polling_interval == 5 and self.callback is None
        return "fixture-token"

    monkeypatch.setattr(captcha_client.TwoCaptcha, "wait_result", wait_result)
    result = captcha_client.sdk_result(
        {
            "key": "fake-key",
            "url": "https://owned.example.test/form",
            "timeout": 300,
            "info": {"kind": kind, "key": "fixture-sitekey"},
        }
    )
    assert result == {"token": "fixture-token"}
    assert len(calls) == 1 and calls[0]["pageurl"] == "https://owned.example.test/form"
    assert (
        calls[0]["method"]
        == {"recaptcha": "userrecaptcha", "hcaptcha": "hcaptcha", "turnstile": "turnstile"}[kind]
    )


async def test_hcaptcha_async_execute_and_render_callback_receive_token_once(config, page, monkeypatch):
    config.twocaptcha_api_key = "fake-key"
    await page.evaluate(HOOK)
    await page.set_content("""<form><div id=widget class=h-captcha data-sitekey=fixture-sitekey></div>
        <textarea name=h-captcha-response></textarea><label>Salary<input value=80000></label></form>""")
    await page.evaluate("""() => {
      window.hcaptcha={render:()=>3,execute:options=>{window.originalOptions=options;return new Promise(()=>{});}};
      hcaptcha.render('widget',{sitekey:'fixture-sitekey',callback:token=>{window.callbackCalls=(window.callbackCalls||0)+1;window.callbackToken=token;}});
      window.resultPromise=hcaptcha.execute({async:true});
    }""")
    provider = AsyncMock(return_value={"token": "fixture-token"})
    monkeypatch.setattr("jobpilot.captcha.solve_token", provider)
    events = []
    solver = CaptchaSolver(config, lambda *a: events.append(a))
    assert await solver.solve(page)
    assert await page.evaluate("window.resultPromise") == {"response": "fixture-token", "key": ""}
    assert await page.evaluate("window.originalOptions") == {"async": True}
    assert await page.evaluate("window.callbackCalls") == 1
    assert await page.locator("textarea").input_value() == "fixture-token"
    assert await page.locator("input").input_value() == "80000"
    assert provider.await_count == 1 and "fixture-token" not in str(events) and "fake-key" not in str(events)
    assert not await solver.solve(page)
    assert provider.await_count == 1


async def test_provider_reply_cannot_be_applied_to_replaced_widget(config, page, monkeypatch):
    config.twocaptcha_api_key = "fake-key"
    await page.evaluate(HOOK)
    await page.set_content("<div id=widget class=h-captcha data-sitekey=first></div>")
    await page.evaluate(
        "window.hcaptcha={render:()=>1};hcaptcha.render('widget',{sitekey:'first',callback:()=>window.accepted=true})"
    )

    async def solve(*args, **kwargs):
        await page.evaluate("window.__jpTokenCaptcha.entries[0].key='second'")
        return {"token": "fixture-token"}

    monkeypatch.setattr("jobpilot.captcha.solve_token", solve)
    solver = CaptchaSolver(config, lambda *a: None)
    assert not await solver.solve(page)
    assert not await page.evaluate("!!window.accepted")


async def test_multiple_widgets_do_not_buy_or_deliver_to_unowned_callback(config, page, monkeypatch):
    config.twocaptcha_api_key = "fake-key"
    await page.evaluate(HOOK)
    await page.set_content(
        "<div id=one class=h-captcha data-sitekey=one></div><div id=two class=h-captcha data-sitekey=two></div>"
    )
    await page.evaluate(
        "window.hcaptcha={render:id=>id};hcaptcha.render('one',{sitekey:'one',callback:()=>{}});hcaptcha.render('two',{sitekey:'two',callback:()=>{}})"
    )
    provider = AsyncMock()
    monkeypatch.setattr("jobpilot.captcha.solve_token", provider)
    assert not await CaptchaSolver(config, lambda *a: None).solve(page)
    provider.assert_not_awaited()
    assert (await page.evaluate(DETECT))["ambiguous"]


async def test_callback_transition_allows_new_widget_without_rebuying_old_one(config, page, monkeypatch):
    config.twocaptcha_api_key = "fake-key"
    await page.set_content("<div id=first class=h-captcha data-sitekey=first></div>")
    await page.evaluate(HOOK)
    await page.evaluate("""() => {
      let id=0;window.hcaptcha={render:()=>++id};
      hcaptcha.render('first',{sitekey:'first',callback:()=>{
        document.getElementById('first').remove();
        document.body.insertAdjacentHTML('beforeend','<div id=second class=h-captcha data-sitekey=second></div>');
        hcaptcha.render('second',{sitekey:'second',callback:()=>window.accepted=true});
      }});
    }""")
    provider = AsyncMock(return_value={"token": "fixture-token"})
    monkeypatch.setattr("jobpilot.captcha.solve_token", provider)
    solver = CaptchaSolver(config, lambda *args: None)
    assert not await solver.has_new_challenge(page)
    assert await solver.solve(page)
    assert await solver.has_new_challenge(page)
    assert await solver.solve(page)
    assert await page.evaluate("!!window.accepted")
    assert not await solver.has_new_challenge(page)
    assert not await solver.solve(page)
    assert provider.await_count == 2


async def test_failed_followup_widget_cannot_buy_again(config, page, monkeypatch):
    config.twocaptcha_api_key = "fake-key"
    await page.set_content("<div id=widget class=h-captcha data-sitekey=new></div>")
    await page.evaluate(HOOK)
    await page.evaluate(
        "window.hcaptcha={render:()=>1};hcaptcha.render('widget',{sitekey:'new',callback:()=>{}})"
    )
    provider = AsyncMock(return_value={"error": "SOLVER_REJECTED"})
    monkeypatch.setattr("jobpilot.captcha.solve_token", provider)
    solver = CaptchaSolver(config, lambda *args: None)
    solver.token_delivered_at[("old-document", "hcaptcha", "old", 1, 1)] = 1
    assert await solver.has_new_challenge(page)
    assert not await solver.solve(page)
    assert not await solver.has_new_challenge(page)
    assert not await solver.solve(page)
    provider.assert_awaited_once()


async def test_cancel_kills_blocking_sdk_poller(monkeypatch):
    started = asyncio.Event()

    class Process:
        returncode = None

        async def communicate(self, data):
            started.set()
            await asyncio.Event().wait()

        def kill(self):
            self.returncode = -9

        async def wait(self):
            return self.returncode

    process = Process()
    monkeypatch.setattr(captcha_client.asyncio, "create_subprocess_exec", AsyncMock(return_value=process))
    task = asyncio.create_task(
        captcha_client.solve_token("fake", {"kind": "hcaptcha"}, "https://example.test", 300)
    )
    await started.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert process.returncode == -9


async def test_automatic_rendered_hcaptcha_captures_pending_execute(config, page, monkeypatch):
    config.twocaptcha_api_key = "fake-key"
    await page.set_content(
        "<form><div class=h-captcha data-sitekey=fixture></div><textarea name=h-captcha-response></textarea></form>"
    )
    await page.evaluate("window.hcaptcha={execute:()=>new Promise(()=>{})}")
    await page.evaluate(HOOK)
    await page.evaluate("window.answer=hcaptcha.execute({async:true});void 0")
    provider = AsyncMock(return_value={"token": "fixture-token"})
    monkeypatch.setattr("jobpilot.captcha.solve_token", provider)
    assert await CaptchaSolver(config, lambda *a: None).solve(page)
    assert await page.evaluate("window.answer") == {"response": "fixture-token", "key": ""}
    provider.assert_awaited_once()


async def test_widget_without_callback_does_not_purchase_token(config, page, monkeypatch):
    config.twocaptcha_api_key = "fake-key"
    await page.set_content("<div class=h-captcha data-sitekey=fixture></div>")
    provider = AsyncMock()
    monkeypatch.setattr("jobpilot.captcha.solve_token", provider)
    assert not await CaptchaSolver(config, lambda *a: None).solve(page)
    provider.assert_not_awaited()


@pytest.mark.parametrize("in_form", [True, False])
async def test_callback_less_recaptcha_checkbox_fills_only_its_form_field(config, page, monkeypatch, in_form):
    # JazzHR: auto-rendered v2 checkbox, no data-callback, response read on the normal submit.
    config.twocaptcha_api_key = "fake-key"
    widget = (
        "<div class=g-recaptcha data-sitekey=fixture>"
        '<textarea name=g-recaptcha-response style="display:none"></textarea></div>'
    )
    await page.set_content(f"<form method=post>{widget}<button>Submit</button></form>" if in_form else widget)
    provider = AsyncMock(return_value={"token": "synthetic-token"})
    monkeypatch.setattr("jobpilot.captcha.solve_token", provider)
    solver = CaptchaSolver(config, lambda *a: None)
    assert await solver.solve(page) is in_form
    value = await page.locator("[name=g-recaptcha-response]").input_value()
    assert value == ("synthetic-token" if in_form else "")
    assert provider.await_count == (1 if in_form else 0)
    if in_form:  # Delivered once; a controller poll does not buy again.
        assert not await solver.solve(page) and provider.await_count == 1


async def test_enterprise_widget_action_is_not_misclassified_as_v3(page):
    await page.set_content("<div id=widget></div>")
    await page.evaluate(HOOK)
    await page.evaluate(
        "window.grecaptcha={enterprise:{render:()=>7,execute:()=>new Promise(()=>{})}};grecaptcha.enterprise.render('widget',{sitekey:'fixture'});window.pending=grecaptcha.enterprise.execute(7,{action:'submit'});void 0"
    )
    info = await page.evaluate(DETECT)
    assert info["enterprise"] and info["action"] == "submit" and not info["v3"]


def test_provider_error_description_never_leaks(monkeypatch):
    def reject(self, **args):
        raise captcha_client.TransportApiException("private key and token fixture")

    monkeypatch.setattr(captcha_client.TwoCaptcha, "hcaptcha", reject)
    result = captcha_client.sdk_result(
        {
            "key": "fake",
            "url": "https://example.test",
            "timeout": 300,
            "info": {"kind": "hcaptcha", "key": "fixture"},
        }
    )
    assert result["error"] == "SOLVER_REJECTED"
    # Only the exception class and provider error codes are kept as diagnostics.
    assert "private" not in json.dumps(result) and "token" not in json.dumps(result)


def test_provider_error_code_is_kept_without_key():
    exc = captcha_client.TransportNetworkException("ERROR_CAPTCHA_UNSOLVABLE /res.php?key=fake&id=1")
    assert captcha_client.detail(exc, "fake") == "NetworkException ERROR_CAPTCHA_UNSOLVABLE"


@pytest.mark.parametrize("kind,class_name", [("hcaptcha", "h-captcha"), ("recaptcha", "g-recaptcha")])
async def test_native_callback_success_preserved_without_another_purchase(
    page, config, monkeypatch, kind, class_name
):
    from jobpilot.forms import FormSession

    config.twocaptcha_api_key = "fake-key"
    await page.set_content(f"<div id=widget class={class_name} data-sitekey=owned-key></div>")
    await page.evaluate(HOOK)
    await page.evaluate(
        """kind => {
      const name=kind==='hcaptcha'?'hcaptcha':'grecaptcha';
      window[name]={render:(node,opts)=>{window.nativeCallback=opts.callback;return 1;}};
      window[name].render('widget',{sitekey:'owned-key',callback:function(token){
        window.called=(window.called||0)+1;window.receiver=this.fixture;return 'kept';
      }});
      window.callbackReturn=window.nativeCallback.call({fixture:'native-receiver'},'native-fixture-token');
    }""",
        kind,
    )
    provider = AsyncMock()
    monkeypatch.setattr("jobpilot.captcha.solve_token", provider)
    solver = CaptchaSolver(config, lambda *a: None)
    assert await page.evaluate("window.called") == 1
    assert await page.evaluate("window.receiver") == "native-receiver"
    assert await page.evaluate("window.callbackReturn") == "kept"
    assert await solver.token_present(page)
    assert not await solver.solve(page)
    assert not await solver.solve(page, pre_submit=True)
    provider.assert_not_awaited()
    assert await FormSession(page, None, None, lambda *a: None).proof() is None


async def test_aged_hcaptcha_must_refresh_before_submit_and_failure_cannot_be_rebought(
    page, config, monkeypatch
):
    config.twocaptcha_api_key = "fake-key"
    await page.set_content("<div id=widget class=h-captcha data-sitekey=owned-key></div>")
    await page.evaluate(HOOK)
    await page.evaluate("""() => {
      hcaptcha={render:(node,opts)=>{window.cb=opts.callback;return 1;}};
      hcaptcha.render('widget',{sitekey:'owned-key',callback:()=>{}});
      cb('native-fixture-token');
      __jpTokenCaptcha.entries[0].deliveredAt=Date.now()-127000;
    }""")
    provider = AsyncMock(return_value={"error": "SOLVER_TIMEOUT"})
    monkeypatch.setattr("jobpilot.captcha.solve_token", provider)
    solver = CaptchaSolver(config, lambda *a: None)
    assert not await solver.solve(page, pre_submit=True)
    assert solver.token_refresh_required
    assert not await solver.solve(page, pre_submit=True)
    provider.assert_awaited_once()


def test_hcaptcha_sdk_receives_only_observed_optional_challenge_data(monkeypatch):
    requests = []
    monkeypatch.setattr(
        "twocaptcha.api.ApiClient.in_", lambda self, **kw: requests.append(kw) or "OK|fixture"
    )
    monkeypatch.setattr(captcha_client.TwoCaptcha, "wait_result", lambda *a: "fixture-token")
    result = captcha_client.sdk_result(
        {
            "key": "fake",
            "timeout": 300,
            "url": "https://owned.example.test/form",
            "info": {
                "kind": "hcaptcha",
                "key": "fixture",
                "invisible": True,
                "rqdata": "private-fixture-data",
            },
        }
    )
    assert result["token"] == "fixture-token"
    assert len(requests) == 1
    assert requests[0]["invisible"] == 1 and requests[0]["data"] == "private-fixture-data"


async def test_old_execute_completion_does_not_clear_new_pending_generation(page):
    await page.set_content("<div id=widget class=h-captcha data-sitekey=fixture></div>")
    await page.evaluate(HOOK)
    await page.evaluate("""() => {
      window.nativeResolvers=[];
      window.hcaptcha={render:()=>1,execute:()=>new Promise(resolve=>nativeResolvers.push(resolve))};
      hcaptcha.render('widget',{sitekey:'fixture'});
      window.first=hcaptcha.execute(1,{async:true});
      window.second=hcaptcha.execute(1,{async:true});
      nativeResolvers[0]({response:'old-generation-token',key:'native-key'});
    }""")
    assert await page.evaluate("window.first") == {"response": "old-generation-token", "key": "native-key"}
    assert (await page.evaluate(DETECT))["kind"] == "hcaptcha"
    assert await page.evaluate("!!window.__jpTokenCaptcha.entries[0].resolve")
    assert not await page.evaluate("!!window.__jpTokenCaptcha.entries[0].delivered")
    await page.evaluate("nativeResolvers[1]({response:'native-fixture-token',key:'native-key'});void 0")
    assert await page.evaluate("window.second") == {"response": "native-fixture-token", "key": "native-key"}
    assert await CaptchaSolver(None, None).token_present(page)


async def test_failed_purchase_is_not_repeated_by_controller_polls(page, config, monkeypatch):
    config.twocaptcha_api_key = "fake-key"
    await page.set_content("<div id=widget class=h-captcha data-sitekey=fixture></div>")
    await page.evaluate(HOOK)
    await page.evaluate(
        "window.hcaptcha={render:()=>1};hcaptcha.render('widget',{sitekey:'fixture',callback:()=>window.accepted=true})"
    )
    provider = AsyncMock(return_value={"error": "SOLVER_TIMEOUT"})
    monkeypatch.setattr("jobpilot.captcha.solve_token", provider)
    solver = CaptchaSolver(config, lambda *a: None)
    for _ in range(5):
        assert not await solver.solve(page)
    provider.assert_awaited_once()
    assert solver.challenge_unverified and not await page.evaluate("!!window.accepted")


async def test_old_widget_token_cannot_verify_another_failed_challenge(page, config, monkeypatch):
    config.twocaptcha_api_key = "fake-key"
    await page.set_content(
        "<div id=old class=h-captcha data-sitekey=old></div><div id=new class=h-captcha data-sitekey=new></div>"
    )
    await page.evaluate(HOOK)
    await page.evaluate("""() => {
      window.callbacks={};window.hcaptcha={render:(node,opts)=>{callbacks[node]=opts.callback;return node;}};
      hcaptcha.render('old',{sitekey:'old',callback:()=>{}});callbacks.old('previous-token');
      hcaptcha.render('new',{sitekey:'new',callback:()=>{}});
    }""")
    monkeypatch.setattr("jobpilot.captcha.solve_token", AsyncMock(return_value={"error": "SOLVER_TIMEOUT"}))
    solver = CaptchaSolver(config, lambda *a: None)
    assert not await solver.solve(page)
    assert solver.challenge_unverified and not await solver.token_present(page)
    # A late native completion for this exact widget is valid; the previous one was not.
    await page.evaluate("callbacks.new('new-native-token')")
    assert await solver.token_present(page)


@pytest.mark.parametrize("event", ["expired-callback", "error-callback", "reset"])
async def test_native_invalidation_clears_handoff_and_preserves_site_handler(
    page, config, monkeypatch, event
):
    config.twocaptcha_api_key = "fake-key"
    config.captcha_max_attempts = 2
    await page.set_content("<div id=widget class=h-captcha data-sitekey=fixture></div>")
    await page.evaluate(HOOK)
    await page.evaluate("""() => {
      window.callbacks={};window.siteCalls=[];
      window.hcaptcha={render:(node,opts)=>{window.callbacks=opts;return 1;},
        reset:function(...args){siteCalls.push({name:'reset',args,receiver:this===hcaptcha});return 'reset-kept';}};
      hcaptcha.render('widget',{sitekey:'fixture',callback:()=>{},
        'expired-callback':function(...args){siteCalls.push({name:'expired-callback',args,receiver:this.fixture});return 'kept';},
        'error-callback':function(...args){siteCalls.push({name:'error-callback',args,receiver:this.fixture});return 'kept';}});
    }""")
    provider = AsyncMock(return_value={"token": "fixture-token"})
    monkeypatch.setattr("jobpilot.captcha.solve_token", provider)
    solver = CaptchaSolver(config, lambda *a: None)
    assert await solver.solve(page) and await solver.token_present(page)
    for attempt in range(2):
        result = await page.evaluate(
            """event => event==='reset' ? hcaptcha.reset(1) :
          callbacks[event].call({fixture:'owned-receiver'},'fixture-error')""",
            event,
        )
        assert result == ("reset-kept" if event == "reset" else "kept")
        assert not await solver.token_present(page)
        assert await solver.solve(page, pre_submit=True) == (attempt == 0)
    assert provider.await_count == 2  # Native generations cannot reset the spend cap.
    calls = await page.evaluate("siteCalls")
    assert len(calls) == 2 and all(c["name"] == event for c in calls)
    assert all(c["receiver"] == (True if event == "reset" else "owned-receiver") for c in calls)


async def test_removed_successful_widget_is_not_a_token_for_new_pending_widget(page):
    await page.set_content("<div id=old class=h-captcha data-sitekey=old></div>")
    await page.evaluate(HOOK)
    await page.evaluate("""() => {
      let id=0;window.hcaptcha={render:(node,opts)=>{if(node==='old')window.acceptOld=opts.callback;return ++id;}};
      hcaptcha.render('old',{sitekey:'old',callback:()=>{}});acceptOld('native-fixture-token');
      document.getElementById('old').remove();
      document.body.insertAdjacentHTML('beforeend','<div id=new class=h-captcha data-sitekey=new></div>');
      hcaptcha.render('new',{sitekey:'new',callback:()=>{}});
    }""")
    assert not await CaptchaSolver(None, None).token_present(page)


@pytest.mark.parametrize("replace", [False, True])
async def test_native_pass_during_purchase_requires_same_widget(page, config, monkeypatch, replace):
    config.twocaptcha_api_key = "fake-key"
    await page.set_content("<div id=widget class=g-recaptcha data-sitekey=owned-key></div>")
    await page.evaluate(HOOK)
    await page.evaluate("""() => {
      grecaptcha={render:(node,opts)=>{window.nativeCallback=opts.callback;return 1;}};
      grecaptcha.render('widget',{sitekey:'owned-key',callback:()=>window.calls=(window.calls||0)+1});
    }""")

    async def solve(*args, **kwargs):
        await page.evaluate("""replace => {
          if (replace) __jpTokenCaptcha.entries[0].generation++;
          nativeCallback('native-token');
        }""", replace)
        return {"token": "purchased-token"}

    monkeypatch.setattr("jobpilot.captcha.solve_token", solve)
    solver = CaptchaSolver(config, lambda *a: None)
    assert await solver.solve(page) is (not replace)
    assert await page.evaluate("window.calls") == 1
    assert solver.challenge_unverified is replace
