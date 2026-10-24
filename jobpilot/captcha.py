"""Token CAPTCHA workflow: official SDK, then the owned widget's callback.

Provider completion is never an application receipt. No image solver, form
submit bypass, global getResponse override or arbitrary callback traversal.
"""

import asyncio
import json
import time
from urllib.parse import urlsplit

from playwright.async_api import Error as PlaywrightError

from .captcha_client import solve_token
from .captcha_twin import mint as twin_mint
from .captcha_observation import (
    PROVIDER_FRAME_JS,
    challenge_frame as challenge_frame,
    checkbox_frame as checkbox_frame,
)

# Grace for a silent hCaptcha pass to deliver after the same-IP attempt, which already waited for a puzzle.
NATIVE_PASS_SECONDS = 2.0
HOOK = r"""(() => {
 if(""" + PROVIDER_FRAME_JS + r""")return;
 const state=window.__jpTokenCaptcha||(window.__jpTokenCaptcha={entries:[],sequence:0});
 if(state.methodHooks===3)return;
 state.methodHooks=3;
 const wrapped=new WeakSet(), watched=new WeakSet();
 // Hosts whose Turnstile token must come from the clean twin browser (captcha_twin_hosts): the site's server refuses
 // the one this browser's own Turnstile produces (Workable 2026-10-23: HTTP 412 on every apply POST).
 const twinHost=()=>(window.__jpTwinHosts||[]).some(h=>location.hostname===h||location.hostname.endsWith('.'+h));
 const wrap=(api,kind,enterprise=false)=>{
  if(!api)return;
  // Providers populate an existing placeholder object after script loading.
  // Capture each method assignment, rather than marking that empty object done.
  if(!watched.has(api)){
   watched.add(api);
   if(kind==='recaptcha'&&!enterprise){
    const d=Object.getOwnPropertyDescriptor(api,'enterprise');
    if(!d||(d.configurable&&!d.get&&!d.set)){
     let value=api.enterprise;
     Object.defineProperty(api,'enterprise',{configurable:true,enumerable:true,
      get:()=>value,set:next=>{value=next;wrap(next,kind,true);}});
     wrap(value,kind,true);
    }
   }
   for(const name of ['render','reset','execute','getResponse']){
    const d=Object.getOwnPropertyDescriptor(api,name);
    if(d&&(!d.configurable||d.get||d.set))continue;
    let value=api[name];
    // Preserve wrappers already installed on retained application pages.
    if(name!=='execute'&&typeof value==='function'&&
       value.toString().includes('state.entries')&&value.toString().includes('original.apply'))wrapped.add(value);
    Object.defineProperty(api,name,{configurable:true,enumerable:true,
     get:()=>value,set:next=>{value=next;wrap(api,kind,enterprise);}});
   }
  }
  if(typeof api.render==='function'&&!wrapped.has(api.render)){const original=api.render;const replacement=function(container,opts={}){
   const node=typeof container==='string'?document.getElementById(container):container;
   const params={...opts};
   const entry={kind,enterprise,key:params.sitekey||node?.dataset.sitekey,
    callback:params.callback||node?.dataset.callback,node,action:params.action,
    cdata:params.cData,s:params.s,rqdata:params.rqdata,
    invisible:params.size==='invisible',generation:++state.sequence};
   const held=kind==='turnstile'&&twinHost();
   if(held)entry.pending=true;  // nothing reaches the site until the twin's token is delivered
   if(entry.callback||held){params.callback=function(...args){
    if(held){entry.held=args[0];entry.pending=!entry.twinToken;return;}
    if(typeof args[0]==='string'&&args[0]){entry.delivered=true;entry.deliveredAt=Date.now();}
    let fn=entry.callback;
    if(typeof fn==='string'){fn=window;for(const part of entry.callback.split('.'))fn=fn?.[part];}
    if(typeof fn==='function')return fn.apply(this,args);
   };}
   for(const name of ['expired-callback','error-callback']){
    const callback=params[name]||node?.getAttribute('data-'+name);
    params[name]=function(...args){
     entry.delivered=false;entry.deliveredAt=null;entry.pending=held&&!entry.twinToken;entry.resolve=null;
     entry.generation=++state.sequence;
     if(held)return;  // the automated browser's own widget failing is expected here; the twin's token is what counts
     let fn=callback;
     if(typeof fn==='string'){fn=window;for(const part of callback.split('.'))fn=fn?.[part];}
     if(typeof fn==='function')return fn.apply(this,args);
    };
   }
   const args=Array.from(arguments);args[1]=params;
   const id=original.apply(this,args);entry.id=id;state.entries.push(entry);
   return id;
  };wrapped.add(replacement);api.render=replacement;}
  if(typeof api.reset==='function'&&!wrapped.has(api.reset)){const original=api.reset;const replacement=function(id){
   const entries=state.entries.filter(e=>e.kind===kind&&e.enterprise===enterprise);
   const targets=id===undefined?entries.slice(0,1):entries.filter(e=>e.id===id);
   for(const entry of targets){entry.delivered=false;entry.deliveredAt=null;entry.pending=false;entry.resolve=null;entry.generation=++state.sequence;}
   return original.apply(this,arguments);
  };wrapped.add(replacement);api.reset=replacement;}
  if(kind==='turnstile'&&typeof api.getResponse==='function'&&!wrapped.has(api.getResponse)){const original=api.getResponse;
   const replacement=function(id){
    const entry=state.entries.find(e=>e.kind==='turnstile'&&(id===undefined||e.id===id));
    if(twinHost()&&entry?.twinToken)return entry.twinToken;
    return original.apply(this,arguments);
   };wrapped.add(replacement);api.getResponse=replacement;}
  if(typeof api.execute==='function'&&!wrapped.has(api.execute)){const original=api.execute;const replacement=function(id,opts={}){
   const args=Array.from(arguments);
   if(kind==='hcaptcha'&&typeof id==='object'){opts=id;id=undefined;}
   const candidates=state.entries.filter(e=>e.kind===kind&&e.enterprise===enterprise&&(id===undefined||e.id===id));
   let entry=candidates.length===1?candidates[0]:null;
   // Automatic hCaptcha rendering can predate this hook on a retained tab.
   // A single observed sitekey still binds its pending execute promise.
   if(!entry&&kind==='hcaptcha'){
    const widgets=[...document.querySelectorAll('.h-captcha[data-sitekey]')];
    const keys=new Map(widgets.map(node=>[node.dataset.sitekey,node]));
    for(const iframe of document.querySelectorAll('iframe[src]')){try{
     const u=new URL(iframe.src),p=new URLSearchParams(u.hash.slice(1));
     if((u.hostname==='hcaptcha.com'||u.hostname.endsWith('.hcaptcha.com'))&&p.get('sitekey')){
      if(id!==undefined&&p.get('id')&&String(id)!==p.get('id'))continue;
      keys.set(p.get('sitekey'),keys.get(p.get('sitekey'))||iframe.closest('[data-sitekey],.h-captcha')||null);
     }
    }catch{}}
    if(keys.size===1){const [key,node]=[...keys][0];
     entry={kind,enterprise,id,key,node,generation:++state.sequence};state.entries.push(entry);}
   }
   if(!entry&&kind==='recaptcha'&&typeof id==='string'&&opts.action){
    entry={kind,enterprise,id,key:id,generation:++state.sequence};state.entries.push(entry);
   }
   if(!entry&&kind==='recaptcha'&&(id===undefined||typeof id==='number')){
    const fields=id===undefined?[...document.querySelectorAll('[name="g-recaptcha-response"]')]:
     [document.getElementById('g-recaptcha-response-'+id)].filter(Boolean);
    const owned=fields.flatMap(field=>[...field.parentElement.querySelectorAll('iframe[src]')].flatMap(iframe=>{
     try{const u=new URL(iframe.src);
      if(!/(^|\.)(google\.com|recaptcha\.net)$/.test(u.hostname)||
         !/recaptcha\/(api2|enterprise)\/anchor/.test(u.pathname)||
         u.pathname.includes('enterprise')!==enterprise||!u.searchParams.get('k'))return [];
      return [{key:u.searchParams.get('k'),node:field.parentElement,
       id:id??Number(field.id.replace('g-recaptcha-response-','')),invisible:u.searchParams.get('size')==='invisible'}];
     }catch{return [];}
    }));
    if(owned.length===1){entry={kind,enterprise,...owned[0],generation:++state.sequence};state.entries.push(entry);}
   }
   if(entry){entry.action=opts.action||entry.action;
    if(kind==='hcaptcha'&&typeof opts.rqdata==='string')entry.rqdata=opts.rqdata;
    entry.pending=true;entry.delivered=false;
    entry.generation=++state.sequence;entry.async=!!opts.async;}
   const native=original.apply(this,args);
   if(!entry||!native?.then)return native;
   const generation=entry.generation;
   const pending=new Promise(resolve=>{entry.resolve=resolve;});
   return Promise.race([native,pending]).then(value=>{
    if(entry.generation===generation&&
       (typeof value==='string'&&value||typeof value?.response==='string'&&value.response)){
      entry.delivered=true;entry.deliveredAt=Date.now();
    }
    return value;
   }).finally(()=>{if(entry.generation===generation){entry.pending=false;entry.resolve=null;}});
  };wrapped.add(replacement);api.execute=replacement;}
 };
 const setup=()=>{wrap(window.hcaptcha,'hcaptcha');
  if(window.grecaptcha!==window.hcaptcha)wrap(window.grecaptcha,'recaptcha');
  wrap(window.grecaptcha?.enterprise,'recaptcha',true);wrap(window.turnstile,'turnstile');};
 for(const name of ['hcaptcha','grecaptcha','turnstile']){
  const d=Object.getOwnPropertyDescriptor(window,name);
  if(d&&(!d.configurable||d.get||d.set))continue;
  let value=window[name];Object.defineProperty(window,name,{configurable:true,enumerable:true,
   get:()=>value,set:next=>{value=next;setup();}});
 }
 setup();setInterval(setup,50);
})()"""

DETECT = r"""(includeDelivered=false) => {
 const visible=e=>!!e?.getClientRects().length&&getComputedStyle(e).visibility!=='hidden';
 // Lever renders a regular hCaptcha into a 0 px container whose provider frames are all visibility:hidden
 // (hCaptcha's own frame=checkbox-invisible): nothing is shown to click, and the site's Submit calls execute()
 // itself. hCaptcha only (sites hide the invisible-reCAPTCHA badge with CSS, and bought reCAPTCHA tokens work);
 // declared-invisible widgets keep their own path (bought tokens pass iCIMS and Oracle steps).
 const shown=e=>{const r=e.getBoundingClientRect();return r.width>0&&r.height>0&&getComputedStyle(e).visibility!=='hidden';};
 const headless=n=>{const frames=n?[...n.querySelectorAll('iframe')]:[];
  return frames.length>0&&!frames.some(shown)&&!shown(n);};
 const captured=window.__jpTokenCaptcha?.entries||[];
 const entries=captured.filter(e=>e.key&&(e.pending||!e.invisible||e.delivered)&&
  (e.pending||!e.node||visible(e.node))&&(!e.delivered||includeDelivered));
 const pending=entries.filter(e=>e.pending);const candidates=pending.length?pending:entries;
 if(candidates.length>1)return {ambiguous:true};
 if(candidates.length===1){const e=candidates[0];const site=e.kind==='hcaptcha'&&!e.invisible&&headless(e.node);return {kind:e.kind,key:e.key,id:e.id,
  enterprise:e.enterprise,v3:!e.node&&e.id===e.key&&!!e.action,action:e.action,cdata:e.cdata,s:e.s,rqdata:e.rqdata,
  invisible:e.invisible,headless:site,dormant:site&&!e.pending,pending:!!e.pending,
  generation:e.generation,delivered:!!e.delivered,delivered_at:e.deliveredAt};}
 const widgets=[...document.querySelectorAll('.h-captcha[data-sitekey],.g-recaptcha[data-sitekey],.cf-turnstile[data-sitekey]')]
  .filter(e=>visible(e)&&!captured.some(c=>c.node===e));
 if(widgets.length>1)return {ambiguous:true};
 if(widgets.length===1){const e=widgets[0];const site=e.matches('.h-captcha')&&e.dataset.size!=='invisible'&&headless(e);
  return {kind:e.matches('.h-captcha')?'hcaptcha':e.matches('.cf-turnstile')?'turnstile':'recaptcha',
  key:e.dataset.sitekey,callback:e.dataset.callback,invisible:e.dataset.size==='invisible',headless:site,
  dormant:e.dataset.size==='invisible'||site};}
 const found=[];
 for(const el of document.querySelectorAll('iframe[src]')){
  try{const u=new URL(el.src);const hash=new URLSearchParams(u.hash.slice(1));
   if((u.hostname==='hcaptcha.com'||u.hostname.endsWith('.hcaptcha.com'))&&hash.get('sitekey'))
    found.push({kind:'hcaptcha',key:hash.get('sitekey')});
   if(/(^|\.)(google\.com|recaptcha\.net)$/.test(u.hostname)&&/recaptcha\/(api2|enterprise)\/anchor/.test(u.pathname))
    found.push({kind:'recaptcha',key:u.searchParams.get('k'),enterprise:u.pathname.includes('enterprise'),invisible:u.searchParams.get('size')==='invisible',dormant:u.searchParams.get('size')==='invisible'});
  }catch{}
 }
 const unique=[...new Map(found.filter(x=>x.key&&!captured.some(e=>e.kind===x.kind&&e.key===x.key)).map(x=>[x.kind+':'+x.key,x])).values()];
 return unique.length===1?unique[0]:unique.length>1?{ambiguous:true}:null;
}"""

DELIVER = r"""({token,info,probe=false}) => {
 const entries=(window.__jpTokenCaptcha?.entries||[]).filter(e=>e.kind===info.kind&&e.key===info.key&&
  e.enterprise===!!info.enterprise&&(info.id==null||e.id===info.id));
 if(entries.length>1)return false;
 const entry=entries[0];
 if(entry&&entry.generation!==info.generation)return false;
 const widgets=[...document.querySelectorAll('[data-sitekey]')].filter(e=>e.dataset.sitekey===info.key);
 const node=entry?.node||(widgets.length===1?widgets[0]:null);
 const callback=entry?.callback||info.callback;
 let fn=callback;
 if(typeof fn==='string'){fn=window;for(const part of callback.split('.'))fn=fn?.[part];}
 // Observed JazzHR shape: a visible reCAPTCHA v2 checkbox with no callback inside
 // the application form; the server reads its own response field on the normal
 // Submit. Only that single owned field is filled; submission and acceptance
 // stay with the application flow. Anything else needs a callback/promise.
 const formField=!entry&&info.kind==='recaptcha'&&!info.enterprise&&!info.invisible&&
  node&&!node.dataset.callback&&!!node.closest('form')&&
  node.querySelectorAll('textarea[name="g-recaptcha-response"]').length===1;
 // No callback/pending promise means we cannot demonstrate the site's handoff.
 // A held Turnstile (twin host) takes the token through its hidden field and getResponse when it has no callback.
 const heldTurnstile=info.kind==='turnstile'&&!!entry&&('held' in entry||!!entry.pending);
 if(typeof fn!=='function'&&!entry?.resolve&&!formField&&!heldTurnstile)return false;
 if(probe)return true;
 const form=node?.closest('form');
 const shared=!!form&&form.querySelectorAll('[data-sitekey]').length>1;
 const scope=shared?node:(form||node||document);
 const names=info.kind==='hcaptcha'?['h-captcha-response','g-recaptcha-response']:
  info.kind==='turnstile'?['cf-turnstile-response']:['g-recaptcha-response'];
 if(!node&&entries.length!==1)return false;
 for(const name of names){const fields=[...scope.querySelectorAll('[name="'+name+'"]')];
  if(shared&&entry?.id!==undefined){const own=document.getElementById(name+'-'+entry.id);if(own)fields.push(own);}
  for(const field of new Set(fields)){
   field.value=token;field.dispatchEvent(new Event('input',{bubbles:true}));field.dispatchEvent(new Event('change',{bubbles:true}));
 }}
 if(entry&&info.kind==='turnstile'){entry.twinToken=token;entry.pending=false;}
 if(entry?.resolve)entry.resolve(info.kind==='hcaptcha'&&entry.async?{response:token,key:''}:token);
 if(typeof fn==='function')fn(token);
 if(entry){entry.delivered=true;entry.deliveredAt=Date.now();}
 return true;
}"""


PROVIDER = {"hcaptcha": "hCaptcha", "turnstile": "Turnstile"}


class CaptchaSolver:
    def __init__(self, config, emit, db=None):
        self.config, self.emit = config, emit
        self.db = db
        self.attempts, self.token_delivered_at = {}, {}
        self.refresh_attempts = set()
        self.same_ip_tried = set()
        self.twin_tried = set()
        self.twin_delivered_at = None  # monotonic time of the last twin token handed to a site
        self.last_twin = None  # (host, kind, key) of the widget that last received a twin token
        self.twin_rejected = set()
        # (kind, sitekey) of widgets the site executes itself; a token bought on the solver's IP is never theirs.
        self.site_executed = set()
        self.last_reason = ""
        self.token_refresh_required = self.challenge_unverified = False
        self.active_challenge = None

    async def install(self, page):
        hosts = [h.strip().lower().lstrip(".") for h in getattr(self.config, "captcha_twin_hosts", "").split(",") if h.strip()]
        if getattr(self.config, "browser_twin_cdp_url", ""):
            await page.add_init_script(f"window.__jpTwinHosts = {json.dumps(hosts)};")
        await page.add_init_script(HOOK)
        for frame in page.frames:
            try:
                await frame.evaluate(HOOK)
            except PlaywrightError:
                continue

    async def token_present(self, page):
        if self.challenge_unverified:
            if not self.active_challenge:
                return False
            frame, info = self.active_challenge
            try:
                return bool(
                    await frame.evaluate(
                        """info=>!!window.__jpTokenCaptcha?.entries.some(e=>
                    e.kind===info.kind&&e.key===info.key&&e.generation===info.generation&&
                    e.enterprise===!!info.enterprise&&(info.id==null||e.id===info.id)&&e.delivered)""",
                        info,
                    )
                )
            except PlaywrightError:
                return False
        for frame in page.frames:
            try:
                info = await frame.evaluate(DETECT, True)
                if info and not info.get("ambiguous") and info.get("delivered"):
                    return True
            except PlaywrightError:
                continue
        return False

    async def passed_natively(self, frame, info, since_ms):
        """Whether the website's own widget delivered a token for this challenge after since_ms (page clock):
        a fresh pass without a puzzle, never an older token that is due for refresh."""
        deadline = time.monotonic() + NATIVE_PASS_SECONDS
        while True:
            try:
                current = await frame.evaluate(DETECT, True)
            except PlaywrightError:
                return False
            if (
                current
                and not current.get("ambiguous")
                and current.get("delivered")
                and (current.get("delivered_at") or 0) > since_ms
                and current.get("kind") == info["kind"]
                and current.get("key") == info["key"]
            ):
                return True
            if time.monotonic() >= deadline:
                return False
            await asyncio.sleep(0.25)

    @staticmethod
    async def document_replaced(frame, origin):
        try:
            return await frame.evaluate("performance.timeOrigin") != origin
        except PlaywrightError:
            return True

    async def has_new_challenge(self, page):
        """An accepted handoff may mount a different widget on the next page."""
        if not self.token_delivered_at:
            return False
        for frame in page.frames:
            try:
                info = await frame.evaluate(DETECT)
            except PlaywrightError:
                continue
            if not info or info.get("ambiguous") or not info.get("key"):
                continue
            signature = (frame.url, info["kind"], info["key"], info.get("id"), info.get("generation"))
            if signature not in self.attempts and not info.get("delivered"):
                return True
        return False

    async def twin_token(self, frame, info, signature):
        """Owner decision 2026-10-19: hand the site's own callback a token minted by the clean twin Chrome
        (captcha_twin). iCIMS sends a browser whose hCaptcha shows a puzzle to its login-only Central page even after
        the puzzle is answered, so on those hosts the twin goes before the same-IP and 2Captcha paths. False falls
        through to them."""
        endpoint = getattr(self.config, "browser_twin_cdp_url", "")
        host = (urlsplit(frame.url).hostname or "").lower()
        hosts = [
            h.strip().lower().lstrip(".")
            for h in getattr(self.config, "captcha_twin_hosts", "").split(",")
            if h.strip()
        ]
        if (
            not endpoint
            or info["kind"] not in {"hcaptcha", "turnstile"}
            or info.get("rqdata")
            or signature in self.twin_tried
            or not any(host == h or host.endswith("." + h) for h in hosts)
            or (host, info["kind"], info["key"]) in self.twin_rejected
            or self.twin_refused_recently(host)
        ):
            return False
        self.twin_tried.add(signature)
        before = frame.url
        try:
            if not await frame.evaluate(DELIVER, {"info": info, "probe": True}):
                return False
        except PlaywrightError:
            return False
        started = time.monotonic()
        result = await twin_mint(endpoint, before, info["key"], kind=info["kind"])
        elapsed = round(time.monotonic() - started, 2)
        if not result.get("token"):
            self.emit(
                "captcha",
                f"Clean twin browser did not mint the {PROVIDER[info['kind']]} token",
                {"reason": str(result.get("error", ""))[:120], "elapsed_seconds": elapsed},
            )
            return False
        try:
            current = await frame.evaluate(DETECT, False)
            same = ("kind", "key", "id", "generation", "enterprise", "invisible")
            unchanged = (
                not frame.is_detached()
                and frame.url == before
                and isinstance(current, dict)
                and all(current.get(k) == info.get(k) for k in same)
            )
            applied = unchanged and await frame.evaluate(DELIVER, {"token": result["token"], "info": info})
        except PlaywrightError:
            applied = False
        if not applied:
            self.emit("captcha", "CAPTCHA changed before the twin browser's token arrived", {"elapsed_seconds": elapsed})
            return False
        self.last_reason = f"Clean twin browser's {PROVIDER[info['kind']]} token delivered to website callback"
        self.emit("captcha", self.last_reason, {"provider": "twin-browser", "elapsed_seconds": elapsed})
        self.token_delivered_at[signature] = self.twin_delivered_at = time.monotonic()
        self.last_twin = (host, info["kind"], info["key"])
        self.token_refresh_required = self.challenge_unverified = False
        return True

    TWIN_REFUSAL_DAYS = 7

    def twin_refused(self):
        """The site answered the twin's token with a new challenge (JPMorgan's Oracle email step, 2026-10-24: twin
        tokens re-challenged on 11 runs, while this browser's own image answer had passed on 10-22). That widget, and its
        host for a week, take this browser's own routes instead."""
        if not self.last_twin:
            return
        self.twin_rejected.add(self.last_twin)
        if self.db is not None:
            from datetime import UTC, datetime

            self.db.set_setting(
                "captcha_twin_refused:" + self.last_twin[0],
                {"at": datetime.now(UTC).isoformat(), "kind": self.last_twin[1]},
            )
        self.emit("captcha", "The website refused the twin browser's token; this browser answers instead")

    def twin_refused_recently(self, host):
        if self.db is None:
            return False
        from datetime import UTC, datetime, timedelta

        at = (self.db.get_setting("captcha_twin_refused:" + host, {}) or {}).get("at", "")
        return bool(at) and at > (datetime.now(UTC) - timedelta(days=self.TWIN_REFUSAL_DAYS)).isoformat()

    async def solve(self, page, *, pre_submit=False):
        self.token_refresh_required = False
        for frame in page.frames:
            try:
                info = await frame.evaluate(DETECT, pre_submit)
            except PlaywrightError:
                continue
            if not info:
                continue
            if info.get("ambiguous"):
                self.last_reason = "Multiple CAPTCHA widgets require an owned selection"
                self.challenge_unverified, self.active_challenge = True, None
                return False
            if not info.get("key"):
                continue
            if info.get("headless"):
                self.site_executed.add((info["kind"], info["key"]))
            # An idle invisible badge is not a challenge. The employer starts
            # execute() after form validation; its captured promise owns delivery.
            if info.get("dormant"):
                continue
            site_run = (info["kind"], info["key"]) in self.site_executed
            if site_run and not await challenge_frame(page):
                # The site's own execute() is still checking this browser; a silent pass submits the form itself.
                self.last_reason = "hCaptcha is checking this browser; no puzzle is shown"
                continue
            signature = (frame.url, info["kind"], info["key"], info.get("id"), info.get("generation"))
            delivered = self.token_delivered_at.get(signature)
            # A native provider solve already handed the token to the employer.
            # Do not buy a second token while that solve is still fresh.
            native_age = time.time() - info["delivered_at"] / 1000 if info.get("delivered_at") else None
            if info.get("delivered") and delivered is None and (native_age is None or native_age < 90):
                continue
            if self.db is not None:
                from .agent_settings import enabled

                if not enabled(self.db, "captcha"):
                    self.last_reason = "The CAPTCHA agent is disabled in System settings"
                    self.challenge_unverified, self.active_challenge = True, (frame, info)
                    return False
            refresh = bool(
                pre_submit
                and info["kind"] in {"recaptcha", "hcaptcha"}
                and delivered is not None
                and time.monotonic() - delivered >= 90
            )
            refresh = refresh or bool(
                pre_submit
                and info["kind"] in {"recaptcha", "hcaptcha"}
                and native_age is not None
                and native_age >= 90
            )
            self.token_refresh_required = refresh
            if delivered is not None and not refresh:
                continue
            # A failed purchase is not repeated just because another controller polls.
            attempts = self.attempts.get(signature, 0)
            widget_attempts = sum(count for key, count in self.attempts.items() if key[:4] == signature[:4])
            if (
                attempts
                and not refresh
                or refresh
                and signature in self.refresh_attempts
                or widget_attempts >= self.config.captcha_max_attempts
            ):
                return False
            self.challenge_unverified, self.active_challenge = True, (frame, info)
            if await self.twin_token(frame, info, signature):
                return True
            if not self.config.twocaptcha_api_key:
                self.last_reason = "2Captcha is not configured"
                return False
            if (
                info["kind"] == "hcaptcha"
                and getattr(self.config, "captcha_same_ip", False)
                and signature not in self.same_ip_tried
            ):
                # A bought hCaptcha token is bound to the solver's IP (Honeywell, JPMorgan). Answer the
                # image challenge in this browser first; the provider then delivers its own token.
                from .captcha_same_ip import solve_same_ip

                self.same_ip_tried.add(signature)
                started = time.monotonic()
                try:
                    page_clock, document = await frame.evaluate("[Date.now(), performance.timeOrigin]")
                except PlaywrightError:
                    page_clock = document = None
                accepted, reason = await solve_same_ip(
                    self.config, page, self.emit, pending=bool(info.get("pending"))
                )
                self.emit(
                    "captcha",
                    "Same-IP hCaptcha " + ("accepted" if accepted else "not completed"),
                    {"reason": str(reason)[:160], "elapsed_seconds": round(time.monotonic() - started, 2)},
                )
                if accepted:
                    self.last_reason = "Website accepted the same-IP hCaptcha answer"
                    self.token_delivered_at[signature] = time.monotonic()
                    self.token_refresh_required = self.challenge_unverified = False
                    return True
                if site_run and document is not None and await self.document_replaced(frame, document):
                    # The site posted from its own callback while the puzzle was answered (Lever); its
                    # receipt or refusal on the new page decides the outcome, not this solver.
                    self.last_reason = "The website moved on while its hCaptcha puzzle was being answered"
                    self.challenge_unverified, self.active_challenge = False, None
                    return False
                if page_clock is not None and await self.passed_natively(frame, info, page_clock):
                    # hCaptcha Enterprise passed this browser without a puzzle (Lever). Its own token is bound to
                    # this browser and IP; a bought token from the solver's IP was what Lever rejected (HTTP 400).
                    self.emit("captcha", "hCaptcha passed this browser without a puzzle")
                    self.last_reason = "hCaptcha accepted this browser without a puzzle"
                    self.token_delivered_at[signature] = time.monotonic()
                    self.token_refresh_required = self.challenge_unverified = False
                    return True
            if site_run:
                # Lever refused every bought token (HTTP 400 on 47 runs); only this browser's own answer can pass.
                self.last_reason = "The hCaptcha puzzle was not solved in this browser"
                return False
            try:
                deliverable = await frame.evaluate(DELIVER, {"info": info, "probe": True})
            except PlaywrightError:
                # iCIMS replaced the challenge's frame after the sign-in click (iPipeline, Hamilton 2026-10-19 crashed
                # here with "Frame was detached"): the page moved on, and the flow re-reads it.
                self.last_reason = "The website replaced the CAPTCHA's page before a token was needed"
                self.challenge_unverified, self.active_challenge = False, None
                return False
            if not deliverable:
                self.last_reason = "CAPTCHA has no captured callback or pending execution"
                self.challenge_unverified = True
                return False
            self.attempts[signature] = attempts + 1
            if refresh:
                self.refresh_attempts.add(signature)
            self.challenge_unverified = True
            self.emit(
                "captcha", "Official 2Captcha SDK requested", {"provider": "2captcha", "type": info["kind"]}
            )
            started, before = time.monotonic(), frame.url
            try:
                user_agent = await frame.evaluate("navigator.userAgent")
            except PlaywrightError:
                user_agent = ""
            result = await solve_token(
                self.config.twocaptcha_api_key,
                info,
                before,
                self.config.captcha_timeout,
                user_agent=user_agent if isinstance(user_agent, str) else "",
            )
            if result.get("error"):
                self.last_reason = "2Captcha: " + result["error"]
                self.emit(
                    "captcha",
                    "CAPTCHA solver failed",
                    {
                        "reason_code": result["error"],
                        "detail": str(result.get("detail", ""))[:200],
                        "elapsed_seconds": round(time.monotonic() - started, 2),
                    },
                )
                return False
            try:
                current = await frame.evaluate(DETECT, pre_submit)
                # Same widget, not a byte-identical snapshot: pending/delivered timestamps move while a
                # 15-100 s solve runs, and an exact comparison discarded valid tokens (Commvault 2026-10-14).
                same = ("kind", "key", "id", "generation", "enterprise", "invisible")
                unchanged = (
                    not frame.is_detached()
                    and frame.url == before
                    and isinstance(current, dict)
                    and all(current.get(k) == info.get(k) for k in same)
                )
                applied = unchanged and await frame.evaluate(
                    DELIVER, {"token": result["token"], "info": info}
                )
            except PlaywrightError:
                applied = False
            self.last_reason = (
                "Token delivered to website callback"
                if applied
                else "CAPTCHA changed or has no owned callback"
            )
            self.emit("captcha", self.last_reason, {"elapsed_seconds": round(time.monotonic() - started, 2)})
            if applied:
                self.token_delivered_at[signature] = time.monotonic()
                self.token_refresh_required = self.challenge_unverified = False
            return bool(applied)
        return False


async def solve(config, page, emit, max_seconds=45):
    solver = CaptchaSolver(config, emit)
    await solver.install(page)
    applied = await solver.solve(page)
    return {"solved": applied, "reason": solver.last_reason}
