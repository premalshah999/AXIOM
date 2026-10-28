() => {
  // Observes one document (and its open shadow roots). IDs are assigned to DOM nodes here and
  // referenced by Python; the model never invents selectors.
  const W = window;
  W.__jpCounter = W.__jpCounter || 0;
  W.__jpDocId = W.__jpDocId || Math.random().toString(36).slice(2, 10);
  const id = el => el.dataset.jpId || (el.dataset.jpId = 'jp' + (++W.__jpCounter));
  const clean = t => (t || '').replace(/\s+/g, ' ').trim();
  // One synchronous observation only. Never reuse these across input events,
  // page transitions or scans: dependent questions must be read afresh.
  const styles = new WeakMap(), visibility = new WeakMap(), captions = new WeakMap();
  const style = el => {
    if (!styles.has(el)) styles.set(el, getComputedStyle(el));
    return styles.get(el);
  };
  const visible = el => {
    if (!el) return false;
    if (!visibility.has(el)) visibility.set(el,
      !!(el.isConnected && el.getClientRects().length && style(el).visibility !== 'hidden'));
    return visibility.get(el);
  };
  const byId = (el, key) => {
    const root = el.getRootNode();
    return (root.getElementById && root.getElementById(key)) || document.getElementById(key);
  };
  const textOf = el => {
    if (!el) return '';
    if (captions.has(el)) return captions.get(el);
    const read = (n, top = false) => {
      if (n.nodeType === Node.TEXT_NODE) return n.textContent || '';
      if (n.nodeType !== Node.ELEMENT_NODE) return '';
      if (!top && (style(n).display === 'none' || style(n).visibility === 'hidden')) return '';
      if (!top && n.matches('input,select,textarea,button,[role=option],script,style')) return '';
      // An SVG's never-rendered fallback: Workable's icons hold "SVGs not supported by this browser." in <desc>,
      // which became part of every checkbox option and no fill matched it (JeffreyM Consulting 2026-10-27).
      if (!top && n.matches('svg desc,svg metadata')) return '';
      const assigned = n.tagName === 'SLOT' ? n.assignedNodes({flatten: true}) : [];
      const children = assigned.length ? assigned : n.shadowRoot ? n.shadowRoot.childNodes : n.childNodes;
      return [...children].map(c => read(c)).join(' ');
    };
    const value = clean(read(el, true));
    captions.set(el, value);
    return value;
  };
  // A file control's own label that names an action, not the document being asked for.
  const GENERIC_UPLOAD = /^(?:attach|upload|choose|browse|select|add)(?: (?:a )?files?)?$|^unlabelled field$/i;
  const GENERIC_PLACEHOLDER = /^[\s\-–—]*(search|select|choose(?!\s+not\s+to\s+disclose)|type|enter|start typing|make a selection|no selection|mm|dd|yyyy)\b|\.\.\.$|…$/i;
  const ownsFile = (box, el) => !!box && ![...box.querySelectorAll('input:not([type=hidden]),select,textarea,[role=combobox],[role=radiogroup]')]
    .some(other => other !== el && !other.contains(el) && !el.contains(other));
  const nearby = el => {
    let node = el;
    // Rippling nests a custom question's control 5-6 levels below the div that
    // holds its text, and a searchable select's input 9 (Go Fish Digital's "What is your desired compensation
    // range?" read "Select", 2026-10-27). Deeper levels are used only while the ancestor owns no
    // other applicant control, so another question's text is never borrowed.
    const CONTROL = 'input:not([type=hidden]),select,textarea,[role=combobox],[role=radiogroup],[role=listbox],[contenteditable=true]';
    for (let depth = 0; depth < 11 && node; depth++) {
      if ((depth >= 3 || el.type === 'file') && [...node.querySelectorAll(CONTROL)].some(x => x !== el && !el.contains(x) && !x.contains(el))) break;
      let prev = node.previousElementSibling;
      while (prev && (!visible(prev) || prev.matches('script,style'))) prev = prev.previousElementSibling;
      if (prev && !prev.matches('input,select,textarea,button') && !prev.querySelector('input,select,textarea,button')) {
        const t = textOf(prev);
        // A long question is still the question when it ends asking one: Collier Aerospace's Rippling citizenship
        // radio group (2026-10-27) read "field-44" behind 330 characters of context ending "...through US citizenship?".
        if (t && (t.length <= 250 || (t.length <= 700 && /\?\s*[*✱]?\s*$/.test(t)))) return t;
      }
      node = node.parentElement;
    }
    return '';
  };
  // A question printed as plain text just before its choices, with no label, legend or ARIA name. The nearest readable
  // text before the first choice (other than a choice's own caption) is the question.
  const precedingText = (first, captions) => {
    const root = first.getRootNode();
    const walker = document.createTreeWalker(root.body || root, NodeFilter.SHOW_TEXT);
    walker.currentNode = first;
    for (let n = walker.previousNode(), steps = 0; n && steps < 40; n = walker.previousNode(), steps++) {
      const t = clean(n.textContent);
      const host = n.parentElement;
      if (!t || !host || host.closest('script,style,noscript,button,a,option') || !visible(host) || captions.has(t)) continue;
      if (!/[a-z]{3}/i.test(t)) continue;  // "*" or "1." beside the question
      return t.length <= 300 ? t : '';
    }
    return '';
  };
  const wrapperLabel = el => {
    const ff = el.closest('[data-automation-id^="formField-"],[data-fkit-id],[data-testid="field"],.ashby-application-form-field-entry');
    if (ff && (el.type !== 'file' || ownsFile(ff, el))) {
      const l = ff.querySelector('label,legend');
      const t = l && textOf(l);
      if (t) return t;
      if (el.type === 'file' && ownsFile(ff, el)) {
        const caption = textOf(ff);
        if (caption && caption.length <= 600) return caption;
      }
    }
    return '';
  };
  // The single visible label/legend of the nearest container that owns only
  // this control. Crosses shadow roots: a Google place picker's search input
  // lives inside <gmpx-place-picker>, beside the field's light-DOM label.
  const ownCaption = el => {
    const hosts=[];
    for(let r=el.getRootNode(); r && r.host; r=r.host.getRootNode())hosts.push(r.host);
    const up=n=>n?.parentElement || n?.getRootNode()?.host || null;
    for (let n=up(el), depth=0; n && depth<(hosts.length?12:6) && n.tagName!=='BODY'; n=up(n),depth++) {
      const controls=[...n.querySelectorAll('input:not([type=hidden]),select,textarea,[role=combobox]')].filter(visible);
      const unique=controls.filter(x=>!controls.some(p=>p!==x&&p.contains(x)));
      if(unique.length>1)return null;
      const captions=[...n.querySelectorAll('label,legend')].filter(visible);
      const owns=unique.length===1 ? (unique[0]===el || unique[0].contains(el))
        : hosts.some(h=>n.contains(h));
      if(captions.length===1 && owns)return captions[0];
      if(captions.length>1)return null;
    }
    return null;
  };
  // A caption marked required by class (job-application-required-field-label)
  // or by a CSS-generated asterisk, with no visible "*" in its text.
  const captionRequired = el => {
    const caption=ownCaption(el);
    if(!caption)return false;
    const cls=n=>typeof n.className==='string'?n.className:'';
    if(/required/i.test(cls(caption)) && !/not[-_ ]?required|optional/i.test(cls(caption)))return true;
    return [caption,...caption.querySelectorAll('*')].some(n=>['::before','::after'].some(p=>
      /^["']?[*✱]["']?$/.test(getComputedStyle(n,p).content)));
  };
  const label = (el, opts = {}) => {
    if (el.matches('input[type=checkbox]') && /^currentlyWorkHere$/i.test(el.name || '')) {
      return 'I currently work here';
    }
    const aria = el.getAttribute('aria-labelledby');
    if (aria) {
      const t = clean(aria.split(/\s+/).map(x => textOf(byId(el, x))).join(' '));
      if (t) return t;
    }
    // Rippling custom questions: <span id="field-12-label"> labels the control
    // id="field-12" without aria-labelledby, while aria-label is only "Select".
    if (el.id) {
      const own = byId(el, el.id + '-label');
      const t = own && clean(textOf(own));
      if (t) return t;
    }
    // A placeholder is not a question ("Select", "Select...", "combobox").
    const rawAl = el.getAttribute('aria-label');
    const al = rawAl && !/^(?:select(?: one| an? option)?(?:\.\.\.|…)?|choose(?: one)?(?:\.\.\.)?|combobox|dropdown|search)$/i.test(rawAl.trim()) ? rawAl : null;
    if (al && !opts.visibleFirst) return clean(al);
    const labels = el.labels ? [...el.labels] : [];
    if (!labels.length && el.id) {
      const root = el.getRootNode();
      const l = root.querySelector && root.querySelector('label[for="' + CSS.escape(el.id) + '"]');
      if (l) labels.push(l);
    }
    if (labels.length) {
      const t = clean(labels.map(textOf).join(' '));
      if (t) return t;
    }
    // Some nested history widgets have a visible label whose `for` points to
    // a wrapper instead of the actual autocomplete input. Associate the nearest
    // single-control container before borrowing a whole section's heading.
    if (!el.matches('input[type=checkbox],input[type=radio],[role=checkbox],[role=radiogroup]')) {
      const caption=ownCaption(el);
      const text=caption && textOf(caption);
      if(text)return text;
    }
    if (el.matches('input[type=checkbox],[role=checkbox]')) {
      let box = el.parentElement;
      for (let i = 0; i < 3 && box; i++, box = box.parentElement) {
        if (box.querySelectorAll('input,[role=checkbox],select,textarea').length > 1) break;
        const t = textOf(box);
        if (t && t.length < 1000) return t;
      }
    }
    // Lever: <div class="application-question"><div class="application-label">Question</div>...
    const appLabel = el.closest('.application-question')?.querySelector('.application-label');
    if (appLabel && !['checkbox', 'radio'].includes(el.type)) {
      const t = textOf(appLabel);
      if (t) return t;
    }
    const legendBox = el.closest('[role=group],fieldset');
    const legend = (el.type !== 'file' || ownsFile(legendBox, el)) && legendBox?.querySelector('legend');
    if (legend && legend.innerText.trim()) return clean(legend.innerText);
    const wrapped = wrapperLabel(el);
    if (wrapped) return wrapped;
    // A phone widget can label its actual country selector only "Search".
    // Read the owned component's semantic identifier, not a sibling's caption.
    const codeOwner = el.closest('[data-testid]')?.closest('[data-testid$="-code"],[data-testid$="_code"]');
    if (codeOwner && /^(?:phone|telephone|mobile)[_-](?:number[_-])?code$/i.test(codeOwner.dataset.testid || '')
        && [...codeOwner.querySelectorAll('input:not([type=hidden]),select,textarea')].every(x => x === el)) {
      return 'Country phone code';
    }
    if (al) return clean(al);
    const placeholder = el.getAttribute('placeholder');
    if (placeholder && !GENERIC_PLACEHOLDER.test(placeholder.trim())) return clean(placeholder);
    const near = nearby(el);
    if (near) return near;
    return clean(placeholder || el.getAttribute('title') || el.getAttribute('name') || el.id || 'Unlabelled field');
  };
  const parentOf = el => el?.parentElement || el?.getRootNode()?.host || null;
  const composedClosest = (el, selector) => {
    for(let n=el;n;n=parentOf(n))if(n.matches?.(selector))return n;
    return null;
  };
  const deepQuery = (root, selector) => {
    if(!root)return [];
    const result=[...root.querySelectorAll(selector)];
    for(const e of root.querySelectorAll('*'))if(e.shadowRoot)result.push(...deepQuery(e.shadowRoot,selector));
    return result;
  };
  const section = el => {
    const legend=textOf(deepQuery(composedClosest(el,'fieldset'),'legend')[0]);
    if(legend)return legend;
    const region=composedClosest(el,'section,[role=region],.form-section');
    const heading=textOf(deepQuery(region,'h2,h3,h4,[role=heading]')[0]);
    if(heading)return heading;
    // Custom ATS sections often use a plain header div, including slotted text.
    // Read only the first child of a section, never its entire form or values.
    const first=region?.firstElementChild;
    // A form container is not a header. Its label/menu text changes when a
    // picker opens, which would give the same question a different identity.
    if(first?.matches('form,input,select,textarea,[role=combobox],[role=listbox]')
      || deepQuery(first,'input,select,textarea,[role=combobox],[role=listbox]').length)return '';
    const header=textOf(first);
    return header && header.length<=160 ? header : '';
  };
  // A career site's own search box: one text box whose only buttons search ("Search now"). TikTok's job page
  // (2026-10-27) shows "Discover a career that energizes and excites you every day." beside "Search now" under its
  // Apply button; read as an application question, the job page became a form with no way forward.
  const searchBox = el => {
    if (!(el.tagName === 'INPUT' && /^(?:text|search|)$/i.test(el.getAttribute('type') || ''))) return false;
    for (let box = el.parentElement, depth = 0; box && depth < 4; box = box.parentElement, depth++) {
      const actions = [...box.querySelectorAll('button,[role=button],input[type=submit]')].filter(visible);
      if (!actions.length) continue;
      const inputs = [...box.querySelectorAll('input,select,textarea')].filter(visible)
        .filter(e => !['hidden','submit','button'].includes((e.type || '').toLowerCase()));
      return inputs.length === 1 && actions.every(b => /^(?:search(?: now| jobs)?|find jobs)$/i
        .test(clean(b.innerText || b.value || b.getAttribute('aria-label') || '')));
    }
    return false;
  };
  const historyMemo = new WeakMap();
  const historyRow = el => {
    for(let p=parentOf(el),depth=0;p && depth<20 && p.tagName!=='BODY';p=parentOf(p),depth++) {
      if(!historyMemo.has(p)) {
        const inputs=deepQuery(p,'input:not([type=hidden]):not([type=checkbox]):not([type=radio]):not([type=password]),select,textarea').filter(visible);
        const labelled=inputs.map(e=>({e,label:clean(label(e)).replace(/\s*[*✱]\s*$/,'')}));
        const companies=labelled.filter(x=>/^(?:company(?: name)?|employer(?: name)?)$/i.test(x.label));
        const titles=labelled.filter(x=>/^(?:(?:job|position) )?title$/i.test(x.label));
        const schools=labelled.filter(x=>/^(?:school|university|college|institution)(?: name)?$/i.test(x.label));
        const dates=labelled.filter(x=>/^(?:from|to|(?:start|end) date(?: (?:year|month))?)$/i.test(x.label));
        // A whole application can contain one history row. Its contact and
        // narrative controls must not inherit that row when climbing ancestors.
        const outsideHistory=labelled.some(x=>/first name|last name|email|phone|hiring team|message to|why (?:us|this)|cover letter/i.test(x.label));
        let row=null;
        if(!outsideHistory && inputs.length<=25 && dates.length && companies.length===1 && titles.length===1 && !schools.length) {
          row={kind:'work',anchor:companies[0].e,identity:{company:companies[0].e.value||'',title:titles[0].e.value||''}};
        } else if(!outsideHistory && inputs.length<=25 && dates.length && schools.length===1 && !companies.length) {
          const degree=labelled.find(x=>/^degree(?: type| level)?$/i.test(x.label));
          row={kind:'education',anchor:schools[0].e,identity:{school:schools[0].e.value||'',degree:degree?.e.value||''}};
        }
        historyMemo.set(p,row);
      }
      const row=historyMemo.get(p);
      if(row)return row;
    }
    return null;
  };
  const groupOf = el => {
    // Workday tenants rename the section; stable input ids identify each row.
    let work = el.closest('[role=group]');
    while (work) {
      const title = work.querySelector('input[id^="workExperience-"][id$="--jobTitle"]');
      if (title) {
        const titles = [...document.querySelectorAll('input[id^="workExperience-"][id$="--jobTitle"]')];
        return 'Work Experience ' + (titles.indexOf(title) + 1);
      }
      work = work.parentElement?.closest('[role=group]');
    }
    // Stable Workday row IDs also exist on school/degree prompts, not just text inputs.
    for (const kind of ['education']) {
      let row=el.closest('[role=group]');
      while(row) {
        const selector=`[id^="${kind}-"][id*="--school" i]`;
        const anchor=row.querySelector(selector);
        // The ID suffix is an internal sequence, not a row number.
        if(anchor) return 'Education '+([...document.querySelectorAll(selector)].indexOf(anchor)+1);
        row=row.parentElement?.closest('[role=group]');
      }
    }
    const education = el.closest('form-builder')?.querySelector('input[name="educationalEstablishment"]');
    if (education?.value) return 'Education at ' + clean(education.value);
    const row=historyRow(el);
    if(row) {
      const anchors=deepQuery(document,'input,select,textarea').filter(visible).filter(e=>row.kind==='work'
        ? /^(?:company(?: name)?|employer(?: name)?)$/i.test(clean(label(e)).replace(/\s*[*✱]\s*$/,''))
        : /^(?:school|university|college|institution)(?: name)?$/i.test(clean(label(e)).replace(/\s*[*✱]\s*$/,'')));
      return (row.kind==='work'?'Work Experience ':'Education ')+(anchors.indexOf(row.anchor)+1);
    }
    let g = composedClosest(parentOf(el),'[role=group]');
    // An upload's own group names it: Jabil's Workday shows two "Upload a file (5MB max)" inputs, one under
    // "Resume/CV/Cover Letter/Supporting Documents" and one under "Additional Attachments" (2026-10-23).
    const named = n => n.getAttribute('aria-labelledby') || n.getAttribute('aria-label') || deepQuery(n,'h2,h3,h4,h5,legend')[0];
    while (g && (el.type === 'file' ? !named(g) : deepQuery(g,'input,select,textarea,button[aria-haspopup]').length < 2)) {
      g = composedClosest(parentOf(g),'[role=group]');
    }
    if (g) {
      const lb = g.getAttribute('aria-labelledby');
      const t = lb ? clean(lb.split(/\s+/).map(x => textOf(byId(g, x))).join(' ')) : '';
      if (t) return t;
      if (g.getAttribute('aria-label')) return clean(g.getAttribute('aria-label'));
      const h = deepQuery(g,'h2,h3,h4,h5,legend')[0];
      if (h) return textOf(h);
    }
    return '';
  };
  const ERR = '[data-automation-id="errorMessage"],[data-automation-id="errorHeading"],[data-automation-id="inputAlert"],[role=alert],.error-message,' +
    '.errorMessage,.field-error,.invalid-feedback,.oj-message-error,.oj-message-detail,.error-text,' +
    '[id$="-error"],[id$="_error"],.iCIMS_Error,.form-error,.text-danger,.input-row__validation';
  const fieldError = (el, groupValid = null) => {
    const out = [];
    const explicit = new Set((el.getAttribute('aria-errormessage') || '').split(/\s+/).filter(Boolean));
    // Native required attributes on every member do not mean that every
    // checkbox in a multiple-choice group must be checked. Its observed
    // selection validates the group; explicit website errors still apply.
    const invalid = el.getAttribute('aria-invalid') === 'true' || groupValid === false
      || (groupValid === null && el.checkValidity && !el.checkValidity());
    const ref = (el.getAttribute('aria-describedby') || '') + ' ' + (el.getAttribute('aria-errormessage') || '');
    for (const key of ref.split(/\s+/).filter(Boolean)) {
      const n = byId(el, key);
      const t = n && visible(n) ? clean(n.innerText) : '';
      // aria-describedby also points at ordinary instructions and examples.
      // Words like "must" or "select" do not make that help text an error.
      if (t && (explicit.has(key) || invalid || n.matches(ERR))
          && /error|required|invalid|must|please|enter|select|valid|format|too (?:short|long)/i.test(t)) out.push(t);
    }
    let box = el.closest('[data-automation-id^="formField-"],.form-group,.form-field,.field,.oj-form-control,.input-row,.question');
    if (box && box.querySelectorAll('input,select,textarea,button[aria-haspopup]').length <= 6) {
      for (const n of box.querySelectorAll(ERR)) if (visible(n)) { const t = clean(n.innerText); if (t) out.push(t); }
    }
    // Web components expose validation in shadow slots, with native validity
    // still true. Stop at the field host so sibling errors stay with siblings.
    const component=composedClosest(el,'spl-internal-form-field');
    if(component) {
      for(const n of deepQuery(component,'slot[name*=error],[slot*=error],'+ERR)) {
        const t=textOf(n);
        if(visible(n) && t && /cannot|invalid|required|must|please|error|too (?:short|long)/i.test(t))out.push(t);
      }
    }
    return [...new Set(out)].join(' ').slice(0, 300);
  };
  // Required: the attribute, ARIA, a "*" or "✱" marker (Lever), or Ashby's required title class.
  const ashbyRequired = el => {
    const entry = el.closest('.ashby-application-form-field-entry,fieldset[class*="ashby-application-form-input-"]');
    const titles = [...(entry?.querySelectorAll('.ashby-application-form-question-title') || [])].filter(visible);
    if(titles.length>1) {
      // A required education/history collection does not make every child
      // question required, especially a boolean "Still Student?" checkbox.
      const meaning=text=>clean(text).replace(/\s*[*✱]\s*$/,'').replace(/[?]$/,'').toLowerCase();
      const own=titles.find(n=>meaning(textOf(n))===meaning(label(el)));
      return !!own && (/required/i.test(own.className || '') || /[*✱]/.test(textOf(own)));
    }
    const title = titles[0];
    return !!entry && (entry.getAttribute('aria-required') === 'true'
      || /required/i.test(entry.className || '')
      || !!title && /required/i.test(typeof title.className === 'string' ? title.className : ''));
  };
  const tidy = q => clean(q).replace(/\s*\.\s*Required\..*$/i, '').replace(/\s*[*✱]\s*$/, '').replace(/\s*\(?\brequired\)?\s*$/i, '').replace(/\s*[*✱]\s*$/, '').trim();
  const pageText = document.body?.innerText || '';
  const missingQuestions = [...pageText.matchAll(/Missing entry for required field:\s*([^\n]+)|The field ["“]([^"”]+)["”] is mandatory/gi)].map(m => tidy(m[1] || m[2]).toLowerCase());
  const serverRequired = q => {
    const question = tidy(q).toLowerCase();
    if (missingQuestions.includes(question)) return true;
    // Taleo validates the entire dependent location/source section rather than each select.
    if (/select ALL possible values in the section Place of Residence/i.test(pageText)
        && /^(country|state\/province|closest major metropolitan region)$/.test(question)) return true;
    return /select a value in the field ["“]Sources["”]/i.test(pageText)
      && /^(source type|sources?|job board|social media)$/.test(question);
  };
  const generatedRequired = (el, q) => {
    const caption = el.closest('[data-testid="field"]')?.previousElementSibling;
    if (!caption || caption.querySelector('input,select,textarea,button')
        || tidy(textOf(caption)) !== tidy(q)) return false;
    return [caption, ...caption.querySelectorAll('*')].some(n => ['::before', '::after'].some(p =>
      /^["']?[*✱]["']?$/.test(getComputedStyle(n, p).content)));
  };
  const isReq = (el, q) => !!el.required || el.getAttribute('aria-required') === 'true' || /[*✱]/.test(q) || generatedRequired(el,q) || ashbyRequired(el) || serverRequired(q) || captionRequired(el)
    || /\.\s*Required\./i.test(q)
    || [...(el.labels || [])].some(l=>/mandatory|required/i.test(l.title || '') || l.querySelector('img.requiredimg'))
    || !!el.closest('.input-row')?.querySelector('.input-row__label--required');
  const CALENDAR_POPUP = '.ui-datepicker,.datepicker-dropdown,.flatpickr-calendar,.pika-single';
  const PLACEHOLDER_VALUE = /^(select( one)?(\.\.\.)?|choose( one)?(\.\.\.)?|-+|[-–—]{1,2}\s*select\s*[-–—]{1,2}|please select|make a selection|none selected|no selection|not specified)$/i;

  // Never applicant fields: inert/hidden subtrees, honeypots, CAPTCHA widgets and their responses.
  const HONEYPOT = /honey.?pot|beecatcher|bot.?trap|leave.?(?:this|blank)|^hp_/i;
  const CAPTCHA_BOX = '.h-captcha,.g-recaptcha,.cf-turnstile,#px-captcha,[data-hcaptcha-widget-id],[data-sitekey]';
  // A select2/chosen <select> is itself aria-hidden behind its visible proxy; that one stays.
  const hiddenTree = el => {
    const h = el.closest('[inert],[aria-hidden="true"]');
    return !!h && !(h === el && el.tagName === 'SELECT');
  };
  const excluded = el => hiddenTree(el) || !!el.closest(CAPTCHA_BOX)
    || !!el.closest('[data-automation-id="languageSelectorButton"],[data-automation-id="settingsSelectorButton"]')
    || el.dataset.automationId === 'beecatcher'
    || HONEYPOT.test(`${el.id || ''} ${el.getAttribute('name') || ''} ${el.className && el.className.baseVal === undefined ? el.className : ''}`)
    || /captcha|recaptcha|turnstile/i.test(`${el.id || ''} ${el.getAttribute('name') || ''}`);

  // Oracle's question blocks label the control only "Please provide a response" and state the question in the
  // block's instructions (Goldman Sachs' government-position question went to the owner without it, 2026-10-18).
  const GENERIC_PROMPT = /^(?:please )?(?:provide|select|choose|enter|give) (?:a |an |your )?(?:response|answer|option|value|selection)[.:*\s]*$/i;
  const blockQuestion = (el, text) => {
    if (!GENERIC_PROMPT.test(clean(text || ''))) return text;
    const block = el.closest('.apply-flow-block');
    const instructions = clean(textOf(block?.querySelector('.apply-flow-block__instructions')) || '');
    const end = instructions.indexOf('?');
    return end > 0 && end < 400 ? instructions.slice(0, end + 1) : instructions.slice(0, 300) || text;
  };

  const all = [];
  const walk = root => { for (const el of root.querySelectorAll('*')) { all.push(el); if (el.shadowRoot) walk(el.shadowRoot); } };
  walk(document);

  const fields = [], consumed = new Set(), seen = new Set();
  const push = (el, f) => {
    const error = fieldError(el, f.type === 'checkboxgroup' ? f.valid ?? null : null);
    const help = (el.getAttribute('aria-describedby') || '').split(/\s+/).filter(Boolean)
      .map(ref=>byId(el,ref)).filter(n=>n && visible(n) && !n.matches(ERR)).map(textOf).join(' ');
    // Ashby renders the actual question in a sibling description without
    // aria-describedby. Scope it to this title's interval, including nested
    // entries that contain several questions, so adjacent questions never leak.
    const entry = el.closest('.ashby-application-form-field-entry');
    const titles = [...(entry?.querySelectorAll('.ashby-application-form-question-title') || [])].filter(visible);
    const precedes = (a,b) => !!(a.compareDocumentPosition(b) & Node.DOCUMENT_POSITION_FOLLOWING);
    const title = titles.filter(t=>t.contains(el) || precedes(t,el)).at(-1);
    const nextTitle = title && titles[titles.indexOf(title)+1];
    const description = title ? [...entry.querySelectorAll('.ashby-application-form-question-description')]
      .filter(n=>visible(n) && precedes(title,n) && (!nextTitle || precedes(n,nextTitle)))
      .map(textOf).join(' ') : '';
    fields.push({
      id: id(el), label: tidy(blockQuestion(el, f.label)),
      // Hamilton's Q1768 is type=text but its iCIMS widget accepts numbers only (2026-10-21).
      type: f.type === 'text' && /\.icims\.com$/i.test(location.hostname)
        && el.classList.contains('iCIMS_Forms_NumberField') ? 'number' : f.type,
      required: !!f.required, options: f.options || [],
      value: f.value == null ? '' : String(f.value), section: section(el), group: groupOf(el),
      history_kind: historyRow(el)?.kind || '', history_identity: historyRow(el)?.identity || {},
      maxlength: f.maxlength ?? (el.maxLength || -1), autocomplete: el.autocomplete || '',
      valid: (f.valid ?? ((el.validity ? el.validity.valid : true) && el.getAttribute('aria-invalid') !== 'true'))
        && !error && !composedClosest(el,'spl-internal-form-field')?.hasAttribute('errorstate'),
      name: el.getAttribute('name') || '', html_id: el.id || '', automation_id: el.dataset.automationId || '',
      placeholder: el.getAttribute('placeholder') || '', error, widget: f.widget || '', search_box: searchBox(el),
      parts: f.parts || [], accept: el.getAttribute('accept') || '', hint: clean([f.hint || '', help, description].join(' ')).slice(0,2500),
      date_format: f.date_format || '',
      members: (f.members || []).map(id),
    });
  };

  // Workday date widgets: month/day/year spinbuttons inside one wrapper.
  for (const wrap of all.filter(e => e.matches('[data-automation-id="dateInputWrapper"],[data-automation-id="dateWidgetContainer"]'))) {
    if (!visible(wrap)) continue;
    const parts = [];
    for (const inp of wrap.querySelectorAll('input')) {
      const key = (inp.dataset.automationId || '') + ' ' + (inp.getAttribute('aria-label') || '');
      const kind = /month/i.test(key) ? 'month' : /day/i.test(key) ? 'day' : /year/i.test(key) ? 'year' : '';
      if (!kind) continue;
      consumed.add(inp);
      const num = inp.getAttribute('aria-valuenow') || (/^\d+$/.test(inp.value) ? inp.value : '');
      parts.push({ kind, dom_id: id(inp), value: num });
    }
    if (!parts.length) continue;
    const order = { month: 0, day: 1, year: 2 };
    parts.sort((a, b) => order[a.kind] - order[b.kind]);
    const value = parts.every(p => p.value) ? parts.map(p => p.kind === 'year' ? p.value.padStart(4, '0') : p.value.padStart(2, '0')).join('/') : '';
    const first = wrap.querySelector('input');
    const q = wrapperLabel(wrap) || label(first, { visibleFirst: true });
    push(wrap, { label: q, type: 'date_parts', required: isReq(first, q) || /[*✱]/.test(wrapperLabel(wrap)), value, parts,
      valid: first.getAttribute('aria-invalid') !== 'true', maxlength: -1 });
    consumed.add(wrap);
  }

  // Workday multiselect "prompt" (How did you hear about us, school, field of study, phone code).
  for (const el of all.filter(e => e.matches('[data-automation-id="multiselectInputContainer"]'))) {
    if (!visible(el) || excluded(el)) continue;
    const input = el.querySelector('input');
    if (input) consumed.add(input);
    const ff = el.closest('[data-automation-id^="formField-"]') || el.parentElement;
    const chosen = [...(ff ? ff.querySelectorAll('[data-automation-id="selectedItem"]') : [])].map(x => clean(x.innerText)).filter(Boolean);
    const q = wrapperLabel(el) || (input ? label(input, { visibleFirst: true }) : label(el));
    push(el, { label: q, type: 'prompt', required: (input && isReq(input, q)) || /[*✱]/.test(q), value: chosen.join(', '),
      maxlength: -1, valid: !(input && input.getAttribute('aria-invalid') === 'true') });
    consumed.add(el);
  }

  // Ashby yes/no button pairs.
  for (const box of all.filter(e => e.matches('.ashby-application-form-input-yesno'))) {
    if (!visible(box) || excluded(box)) continue;
    const group = [...box.querySelectorAll('button[aria-pressed],button')].filter(visible);
    if (!group.length) continue;
    group.forEach(b => consumed.add(b));
    box.querySelectorAll('input').forEach(i => consumed.add(i));
    const entry = box.closest('.ashby-application-form-field-entry');
    const titles = [...(entry?.querySelectorAll('.ashby-application-form-question-title') || [])].filter(visible);
    // Some nested collections use one field-entry for multiple questions.
    // The first title can be Country while this pair answers work eligibility.
    // Bind the nearest preceding question title in that same collection.
    const title = titles.filter(t=>t.contains(box) ||
      (t.compareDocumentPosition(box) & Node.DOCUMENT_POSITION_FOLLOWING)).at(-1);
    const q = title ? clean(title.innerText) : label(box, { visibleFirst: true });
    const pressed = group.find(b => b.getAttribute('aria-pressed') === 'true');
    push(box, { label: q, type: 'buttonchoice', options: group.map(b => clean(b.innerText)),
      required: isReq(box, q),
      value: pressed ? clean(pressed.innerText) : '', maxlength: -1, valid: true });
    consumed.add(box);
  }

  // ADP MyJobs (2026-10-27): "Phone Type" and "State" are sdf-select-simple custom elements; their filter input and
  // listbox stay hidden in a floating pane until the host is clicked, so no field was observed and both stayed empty.
  // The phone component's own country picker is one too, inside its shadow root: never a question of its own.
  for (const host of all.filter(e => e.matches('sdf-select-simple') && !e.getRootNode().host && visible(e))) {
    if (excluded(host) || consumed.has(host)) continue;
    const items = [...host.querySelectorAll('sdf-select-item'), ...(host.shadowRoot?.querySelectorAll('sdf-select-item') || [])];
    // Angular sets ADP's item labels as properties, not attributes (Midco 2026-10-27).
    const itemText = i => clean((typeof i.label === 'string' && i.label) || i.getAttribute('label') || i.textContent);
    const raw = typeof host.value === 'string' ? host.value : (host.value?.label || host.value?.value || '');
    const chosen = items.find(i => i.hasAttribute('selected') || i.getAttribute('aria-selected') === 'true')
      || items.find(i => raw && (i.getAttribute('value') === raw || i.value === raw || itemText(i) === raw));
    const value = chosen ? itemText(chosen) : '';
    const q = label(host, { visibleFirst: true });
    const req = isReq(host, q) || host.hasAttribute('required');
    consumed.add(host);
    // Each item is listed twice (a light-DOM copy and the rendered one); duplicates made "Cell" ambiguous (Midco
    // 2026-10-28). The host's items property can name choices beyond the rendered page.
    const catalog = (Array.isArray(host.items) ? host.items : []).flatMap(i => Array.isArray(i?.items) ? i.items : [i])
      .map(i => clean(String(i?.label ?? '')));
    push(host, { label: q, type: 'combobox', widget: 'sdf-select', required: req,
      options: [...new Set([...catalog, ...items.map(itemText)].filter(Boolean))],
      value, valid: !req || !!value, maxlength: -1 });
  }

  // BambooHR's Fabric pick lists (Performance Software, 2026-10-27): a toggle button[aria-haspopup] opens the menu, and
  // a zero-size aria-hidden <select> beside it mirrors only the chosen value's id for the form post. Observed as the
  // select, State/Country/Highest Education offered nothing to pick and stayed empty: the toggle is the field.
  for (const mirror of all.filter(e => e.matches('select[aria-hidden="true"]:not([multiple])'))) {
    if (consumed.has(mirror) || excluded(mirror) || mirror.disabled) continue;
    if ([...mirror.options].filter(o => o.value !== '').length > 1) continue; // a select2/chosen list, not a mirror
    const toggles = [...(mirror.parentElement?.querySelectorAll('button[aria-haspopup]:not([aria-haspopup="false"])') || [])]
      .filter(b => visible(b) && !consumed.has(b));
    if (toggles.length !== 1) continue;
    const toggle = toggles[0];
    if (toggle.disabled || toggle.getAttribute('aria-disabled') === 'true') continue;
    const shown = clean(toggle.innerText);
    const value = PLACEHOLDER_VALUE.test(shown) ? '' : shown;
    const q = label(mirror);
    const req = isReq(mirror, q);
    consumed.add(mirror);
    consumed.add(toggle);
    push(toggle, { label: q, type: 'dropdown', required: req, options: [], value, valid: !req || !!value, maxlength: -1 });
  }

  // Ashby gives each checkbox its own name; the fieldset defines the question.
  for (const box of all.filter(e => e.matches('.ashby-application-form-input-checkbox-group') && visible(e))) {
    const group = [...box.querySelectorAll('input[type=checkbox]')];
    if (!group.length || excluded(box)) continue;
    const title = box.querySelector('.ashby-application-form-question-title,label,legend');
    const q = title ? textOf(title) : label(box);
    const optionLabel = c => clean([...(c.labels || [])].map(textOf).join(' ')) || c.value;
    const req = isReq(box, q) || !!title?.className.includes('_required_');
    const value = group.filter(c=>c.checked).map(optionLabel).join('\n');
    group.forEach(c=>consumed.add(c));consumed.add(box);
    const count = q.match(/select\s+(\d+)\b/i);
    const validCount = !count || group.filter(c=>c.checked).length === Number(count[1]);
    push(box, {label:q,type:'checkboxgroup',required:req,options:group.map(optionLabel),value,members:group,
      valid:(!req || !!value) && validCount,widget:'container-checkboxes',maxlength:-1,
      hint:count ? `Select exactly ${count[1]} choices` : ''});
  }

  // Checkboxes sharing one name are one multiple-choice question (Lever, Greenhouse).
  // Some Workday groups have no input names; their fieldset owns the question.
  for (const box of all.filter(e => e.matches('fieldset,[role=group]') && visible(e))) {
    const group = [...box.querySelectorAll('input[type=checkbox]')];
    if (group.length < 2 || group.some(c=>consumed.has(c)) || excluded(box)
        || box.querySelector('input:not([type=checkbox]):not([type=hidden]),select,textarea')) continue;
    const q = box.querySelector('legend') ? textOf(box.querySelector('legend')) : label(box,{visibleFirst:true});
    if (!q || q === 'Unlabelled field' || q === box.id) continue;
    // Oracle CX disclosure questions render their choices as unnamed checkboxes in one labelled .input-row group
    // (Rice 2026-10-27: each option read as its own required box and the page never validated).
    if (!/CheckboxGroup/i.test(box.dataset.automationId || '') && !box.matches('.input-row[role=group]')
        && !/select all|choose all|check all|select \d|which (?:locations|interests)|where.{0,40}relocat/i.test(q)) continue;
    const options = group.map(c=>label(c));
    const value = group.filter(c=>c.checked).map(c=>label(c)).join('\n');
    const req = isReq(box,q) || group.some(c=>c.required || c.getAttribute('aria-required')==='true');
    group.forEach(c=>consumed.add(c));box.querySelectorAll('[role=checkbox]').forEach(c=>consumed.add(c));consumed.add(box);
    push(box,{label:q,type:'checkboxgroup',required:req,options,value,members:group,valid:!req || !!value,
      widget:'container-checkboxes',maxlength:-1});
  }

  const boxNames = {};
  for (const c of all.filter(e => e.matches('input[type=checkbox][name]'))) {
    // Repeating employment/education rows reuse input names. Their independent
    // booleans must never become one multi-choice question across the whole page.
    const owner = c.closest('fieldset,[role=group],.question,.application-question,.ashby-application-form-question');
    const key = c.name + '|' + (owner ? id(owner) : groupOf(c));
    (boxNames[key] = boxNames[key] || []).push(c);
  }
  // JazzHR: one question ("Were you referred ...?*" on a hidden input) with separate Yes/No
  // checkboxes named resumator-checkbox-<question>-<n>. They are one choice, not two booleans
  // (Healthpeak 2026-10-14: both got the question label and "No" could never be ticked).
  const jazz = {};
  for (const c of all.filter(e => e.matches('input[type=checkbox]') && /^resumator-checkbox-\d+-\d+$/.test(e.name || ''))) {
    const qid = c.name.split('-')[2];
    (jazz[qid] = jazz[qid] || []).push(c);
  }
  for (const [qid, group] of Object.entries(jazz)) {
    if (group.length < 2 || consumed.has(group[0])) continue;
    const qlabel = document.getElementById('resumator-questionnaire-q' + qid + '-label');
    const raw = clean(qlabel ? qlabel.textContent : '');
    if (!raw) continue;
    group.forEach(c => consumed.add(c));
    const q = raw.replace(/\s*[*✱]+\s*$/, '').trim();
    const req = /[*✱]/.test(raw);
    const value = group.filter(c => c.checked).map(c => label(c)).join('\n');
    push(group[0], { label: q, type: 'checkboxgroup', options: group.map(c => label(c)), required: req, members: group,
      value, maxlength: -1, valid: !req || !!value });
  }
  for (const [name, group] of Object.entries(boxNames)) {
    if (group.length < 2 || excluded(group[0]) || consumed.has(group[0])) continue;
    const shown = group.filter(c => visible(c) || (c.labels && c.labels[0] && visible(c.labels[0])));
    if (!shown.length) continue;
    group.forEach(c => consumed.add(c));
    const parent = group[0].closest('.application-question,fieldset,[role=group]');
    const q = clean(parent?.querySelector('.application-label,legend')?.innerText || '') || label(group[0]) || name;
    const req = group.some(c => c.required || c.getAttribute('aria-required') === 'true') || /[*✱]/.test(q);
    const value = group.filter(c => c.checked).map(c => label(c)).join('\n');
    push(group[0], { label: q, type: 'checkboxgroup', options: group.map(c => label(c)), required: req,members:group,
      value, maxlength: -1, valid: !req || !!value });
  }

  // Oracle's multi-select pills omit aria-pressed before the first selection.
  // Keep the complete question and every explicit member, not individual buttons.
  for (const box of all.filter(e => e.matches('.cx-multi-select-pills') && visible(e))) {
    const group = [...box.querySelectorAll('button.cx-select-pill-section')].filter(visible);
    if (!group.length || excluded(box)) continue;
    const q = label(box.querySelector('.cx-select-pills-container') || box, {visibleFirst:true});
    const chosen = group.filter(b => b.getAttribute('aria-pressed') === 'true'
      || b.classList.contains('cx-select-pill-section--selected'));
    const req = isReq(box,q);
    push(box,{label:q,type:'checkboxgroup',required:req,options:group.map(b=>clean(b.innerText)),
      value:chosen.map(b=>clean(b.innerText)).join('\n'),members:group,widget:'oracle-multi-pills',
      maxlength:-1,valid:!req || chosen.length>0});
    group.forEach(b=>consumed.add(b));consumed.add(box);
  }

  // Oracle Candidate Experience pills and generic aria-pressed button groups.
  const pillGroups = new Set();
  for (const b of all.filter(e => e.matches('button[aria-pressed],[role=button][aria-pressed]'))) {
    if (consumed.has(b)) continue;
    const container = b.closest('cx-select-pills,.cx-select-pills-container,[role=group],fieldset,ul') || b.parentElement;
    if (!container || pillGroups.has(container)) continue;
    const pills = [...container.querySelectorAll('button[aria-pressed],[role=button][aria-pressed]')].filter(visible);
    if (pills.length < 2 || container.querySelector('input:not([type=hidden]),select,textarea')) continue;
    if (pills.some(p => !clean(p.innerText))) continue; // Carousel dots/bookmark icons are navigation.
    pillGroups.add(container);
    pills.forEach(p => consumed.add(p));
    const q = label(container, { visibleFirst: true });
    const pressed = pills.find(p => p.getAttribute('aria-pressed') === 'true');
    push(container, { label: q, type: 'pills', required: isReq(container, q) || /[*✱]/.test(nearby(container)),
      options: pills.map(p => clean(p.innerText)), members: pills,
      value: pressed ? clean(pressed.innerText) : '', maxlength: -1, valid: true });
    consumed.add(container);
  }

  // SmartRecruiters slots its radios into an internal shadow fieldset. Observe the outer
  // question once; native descendants and the empty internal fieldset are the same control.
  for (const host of all.filter(e => e.matches('spl-radio-group') && visible(e))) {
    const radios = [...host.querySelectorAll('spl-radio[role=radio]')];
    if (!radios.length) continue;
    const q = textOf(host.querySelector('[slot=label-content]')) || host.getAttribute('label') || label(host);
    const chosen = radios.find(r => r.getAttribute('aria-checked') === 'true');
    const optionLabel = r => clean(r.getAttribute('label') || textOf(r));
    push(host, {label:q, type:'radiogroup', required:host.hasAttribute('required'),
      options:radios.map(optionLabel), value:chosen ? optionLabel(chosen) : '', widget:'spl-radio', members:radios});
    for (const node of all) {
      for(let n=node; n; n=n.parentElement || n.getRootNode()?.host) {
        if(n===host) {consumed.add(node);break;}
      }
    }
  }

  // Custom role=radio items without a radiogroup wrapper.
  for (const r of all.filter(e => e.matches('[role=radio]') && !e.closest('[role=radiogroup]'))) {
    if (consumed.has(r)) continue;
    const container = r.closest('[role=group],fieldset') || r.parentElement;
    if (!container || seen.has(container) || !visible(r)) continue;
    seen.add(container);
    const group = [...container.querySelectorAll('[role=radio]')];
    group.forEach(x => consumed.add(x));
    const q = label(container, { visibleFirst: true });
    const on = group.find(x => x.getAttribute('aria-checked') === 'true');
    push(container, { label: q, type: 'radiogroup', required: isReq(container, q) || /[*✱]/.test(q) || container.getAttribute('aria-invalid') === 'true',
      options: group.map(x => clean(x.innerText) || label(x)), value: on ? clean(on.innerText) || label(on) : '',
      maxlength: -1, valid: true, members:group });
    consumed.add(container);
  }

  const controls = all.filter(e => e.matches(
    'input,select,textarea,[role=combobox],[role=radiogroup],[role=checkbox],[role=switch],' +
    'button[aria-haspopup=listbox],[role=button][aria-haspopup=listbox],button[class*=select]:not([aria-haspopup])'));
  // A plain button standing in for a <select>: Deloitte's tax-credit questionnaire (Caesars, 2026-10-27) renders "State
  // in which you plan to work" as <button class="wotc-select-input select-btn">Select one... <span arrow-down></button>
  // with no ARIA popup and role-less rows; the question was never observed and Continue stayed disabled.
  const selectButton = el => el.matches('button[class*=select]:not([aria-haspopup]):not([role])')
    && [...el.classList].some(c => /(?:^|[-_])select(?:[-_](?:btn|button|toggle|trigger|input))?$/i.test(c))
    && !!el.querySelector('[class*=arrow],[class*=caret],[class*=chevron]')
    && !el.parentElement?.querySelector('select');
  const optionSearch = el => el.matches('input,textarea') && (
    el.id === 'countryCodeSearch' || el.name === 'countryCodeSearch'
    || (composedClosest(el,'[role=listbox],[role=menu],.select2-dropdown,.select2-search,.chosen-search,.chosen-drop')
      && (el.type === 'search' || el.getAttribute('role') === 'searchbox'
        || /search|filter/i.test(`${el.id || ''} ${el.name || ''} ${el.className || ''}`)))
  );
  for (const el of controls) {
    if (consumed.has(el) || el.closest('[data-automation-id="multiselectInputContainer"]') || excluded(el) || optionSearch(el)) continue;
    // iCIMS retains an AJAX select containing only the current selection.
    // Its visible, paired combobox owns the complete menu; observing both
    // creates duplicate questions and tries to select unloaded native options.
    if (el.matches('select[icimsdropdown-enabled="1"]')) {
      const proxy = byId(el, el.id + '_icimsDropdown');
      if (proxy?.matches('[role=combobox]') && visible(proxy)) continue;
    }
    const role = el.getAttribute('role');
    let type = ['combobox', 'radiogroup'].includes(role) ? role : (el.type || el.tagName.toLowerCase());
    if (el.matches('input[aria-autocomplete="list"][aria-haspopup="listbox"]')) type = 'combobox';
    if (el.matches('[aria-haspopup=listbox]') && !el.matches('input,[role=combobox]') || selectButton(el)) type = 'dropdown';
    if (el.matches('[role=checkbox],[role=switch]') && !el.matches('input')) type = 'checkbox';
    if (el.disabled || el.getAttribute('aria-disabled') === 'true') continue;
    if (el.readOnly && !['combobox','radio','checkbox'].includes(type)) continue;
    if (el.matches('input[type=radio]') && el.closest('[role=radiogroup]')) continue;
    if (['hidden', 'submit', 'button', 'reset', 'image'].includes(type)) continue;
    let widget = '';
    let dateFormat = '';
    let dateParts = [];
    if (type === 'text' && el.matches('input')) {
      const placeholder = el.getAttribute('placeholder') || '';
      const dateClass = /(?:^|[\s_-])(?:datepicker|date-picker|input-date)(?:$|[\s_-])/i.test(el.className || '');
      const picker = composedClosest(el,'spl-date-picker');
      if (picker?.getAttribute('type') === 'month-year' || /^(?:mm[\/.-]yyyy|yyyy[\/.-]mm)$/i.test(placeholder.trim())) {
        widget = 'date-text';
        dateFormat = 'month-year';
        dateParts = [{kind:'month'},{kind:'year'}];
      } else if (dateClass || /^(?:pick|select|choose)(?: a)? date(?:\.\.\.)?$/i.test(placeholder.trim())
          || /^(?:mm[\/.-]dd[\/.-]yyyy|yyyy[\/.-]mm[\/.-]dd|dd[\/.-]mm[\/.-]yyyy)$/i.test(placeholder.trim())) {
        widget = 'date-text';
        dateFormat = /^yyyy/i.test(placeholder) ? 'ymd' : /^dd/i.test(placeholder) ? 'dmy' : 'mdy';
        // Without a placeholder the question names the format: Axtria's JazzHR datepicker asks "Completion date of
        // your highest degree (mm/yyyy)" and a day-month-year answer went to the owner (2026-10-24).
        if (!placeholder.trim() && /(?:^|[^\w\/.-])mm\s*[\/.-]\s*yyyy\b/i.test(clean(label(el)))) {
          dateFormat = 'month-year';
          dateParts = [{kind:'month'},{kind:'year'}];
        }
      }
    }
    // SuccessFactors pick lists (UI5 Web Components under scoped tag names, ASSA ABLOY 2026-10-15):
    // a value-help input keeps only an entry picked from its list, and a ui5-select renders its
    // combobox inside the host, which carries the unscoped tag as an attribute.
    if (type === 'text' && !widget && el.matches('input.ui5-input-inner[aria-haspopup=dialog]')) {
      type = 'combobox';
      widget = 'ui5wc-valuehelp';
    }
    if (type === 'combobox' && el.getRootNode().host?.hasAttribute('ui5-select')) widget = 'ui5wc-select';
    if (!visible(el) && type !== 'file') {
      if (type === 'select-one' || type === 'select-multiple') {
        const proxy = el.nextElementSibling;
        const host = el.parentElement;
        const plugin = (proxy && proxy.matches('.select2,.select2-container,.chosen-container,.selectize-control,.bootstrap-select,.dropdown,.ui-selectmenu-button,[role=combobox],button,a') && visible(proxy) && proxy)
          || (host && [...host.children].find(c => c !== el && visible(c) && c.matches('.select2-container,.chosen-container,.selectize-control,.bootstrap-select,.ui-selectmenu-button,.dropdown-toggle,[class*=select]')));
        if (!plugin) continue;
        widget = 'proxy';
      } else if (type === 'checkbox' || type === 'radio') {
        const lab = (el.labels && el.labels[0])
          || byId(el, (el.getAttribute('aria-labelledby') || '').split(/\s+/)[0] || '_');
        if (!lab || !visible(lab)) continue;
        widget = 'label';
      } else continue;
    }
    if (el.closest('[role=combobox]') !== el && el.closest('[role=combobox]')) continue;
    let options = [], value = el.value || '', question = label(el), group = null, uploadRequired = false;
    if (type === 'combobox' && el.matches('input.cx-multi-select-input')) {
      // CX clears the search query after selecting; sibling pills hold the answer.
      const root = el.closest('.cx-multi-select-container')?.parentElement;
      if (root?.querySelectorAll('input.cx-multi-select-input').length === 1) {
        widget = 'oracle-multiselect';
        value = [...root.querySelectorAll('.cx-multi-select-pill__value-text')]
          .map(x => clean(x.textContent)).filter(Boolean).join('\n');
      }
    }
    if (type === 'combobox' && el.matches('.c-spl-multiselect-autocomplete-input')) {
      // The search input clears after a selection; the committed answer lives in slotted tags.
      widget = 'spl-multiselect';
      let host = el;
      while (host && host.tagName !== 'SPL-MULTISELECT-AUTOCOMPLETE') host = host.parentElement || host.getRootNode()?.host;
      const choices = [...(host?.shadowRoot?.querySelectorAll('spl-select-option[value]') || [])];
      options = choices.map(c => clean(c.textContent)).filter(Boolean);
      const selected = new Set(Array.isArray(host?.value) ? host.value : []);
      value = choices.filter(c => selected.has(c.getAttribute('value'))).map(c => clean(c.textContent)).join('\n');
    }
    if (el.matches('input.location-input') && el.parentElement.querySelector('input[name=selectedLocation]')) {
      type = 'combobox'; widget = 'lever-location';
      value = el.parentElement.querySelector('input[name=selectedLocation]').value ? el.value : '';
    }
    if (type === 'dropdown') {
      question = label(el, { visibleFirst: true });
      const txt = clean(el.innerText || el.value);
      value = PLACEHOLDER_VALUE.test(txt) ? '' : txt;
      if (question === clean(el.getAttribute('aria-label') || '') && value) {
        question = clean(question.replace(value, '').replace(/\brequired\b/i, '')) || question;
      }
    } else if (type === 'radio') {
      const radioOwner = el.form || el.getRootNode();
      const key = (el.name || id(el)) + '|' + (el.form ? id(el.form) : 'document');
      if (seen.has(key)) continue;
      seen.add(key);
      const root = el.getRootNode();
      group = el.name ? [...root.querySelectorAll('input[type=radio]')].filter(x => x.name === el.name && (x.form || x.getRootNode()) === radioOwner) : [el];
      // Lever wraps radios in .application-question with an .application-label (no fieldset).
      const parent = el.closest('fieldset,[role=radiogroup],.application-question');
      question = parent ? (parent.querySelector('legend,.application-label,.ashby-application-form-question-title')?.innerText || label(parent)) : '';
      if (!question || question === 'Unlabelled field' || question === parent?.id) {
        question = wrapperLabel(el) || (parent && nearby(parent)) || nearby(el.parentElement || el) || el.name || label(el);
      }
      // MAXIMUS's tax-credit survey (PepsiCo, 2026-10-24) prints "Are you a resident of the State of California?" as
      // plain text above an unlabelled Yes/No pair: the question read as "ctl00$MainContent$multipleChoice...".
      if ((question === el.name || question === 'Unlabelled field') && !/^icims_f_/i.test(el.name || '')) {
        question = precedingText(group[0], new Set(group.map(x => clean(label(x))))) || question;
      }
      // iCIMS's self-identification forms (icims2 servlet) give radios no label: every choice and the question read as
      // the field name "icims_f_Veteran" (ICE 2026-10-19). A readable value is the choice; the name names the topic.
      // Its veteran choices are codes (NotProtectedVeteran is what a person submitted at REI Systems); their wording is
      // the standard VEVRAA text, so the applicant's veteran answer can match them.
      const SELF_ID = {ProtectedVeteran: 'I identify as one or more of the classifications of protected veteran',
        NotProtectedVeteran: 'I am not a protected veteran', optout: "I don't wish to answer"};
      const radioText = x => {
        const t = label(x);
        const bare = !t || t === x.name || t === x.id || t === 'Unlabelled field';
        if (!(bare && x.value && x.value !== 'on' && /[a-z]{2}/i.test(x.value))) return t;
        return clean(SELF_ID[x.value] || x.value);
      };
      if (question === el.name && /^icims_f_[a-z]/i.test(el.name || '')) {
        question = el.name.replace(/^icims_f_/i, '').replace(/_/g, ' ') + ' status';
      }
      options = group.map(radioText);
      // iCIMS's Forms module (Cooper Health's employment application, 2026-10-22) prints "(o) Yes (o) No" after the
      // question in one cell: each radio read its neighbour's caption, giving [question, "Yes"] or ["Yes", "Yes"],
      // and no choice matched. Readable values ("Yes"/"No") are the choices then.
      if (/^icims_f_/i.test(el.name || '') && group.every(x => x.value && x.value !== 'on' && /[a-z]{2}/i.test(x.value))
          && (new Set(options).size < options.length || options.some(o => o === question || o.length > 60))) {
        options = group.map(x => clean(SELF_ID[x.value] || x.value));
      }
      value = group.find(x => x.checked); value = value ? options[group.indexOf(value)] : '';
      // A phone field can contain a separate SMS-consent radio group. Its
      // question title belongs to the phone input, not these consent choices.
      if (!parent && /(?:text messages?|sms|marketing|newsletter)/i.test(options.join(' '))) {
        question = options.find(o => /^yes\b/i.test(o))?.replace(/^yes\s*[-–:]?\s*/i, '') || el.name;
      }
    } else if (type === 'radiogroup') {
      group = [...el.querySelectorAll('[role=radio]')];
      if (!group.length) group = [...el.querySelectorAll('input[type=radio]')];
      // UI5 Web Components radio buttons (scoped tags on SuccessFactors, ASSA ABLOY 2026-10-15) keep
      // role=radio and their native input in shadow roots; the host has the option text and checked.
      if (!group.length) group = [...el.querySelectorAll('[ui5-radio-button]')];
      // Deloitte's tax-credit questionnaire (Caesars, 2026-10-27) nests three role=radiogroup elements around one
      // Yes/No pair: each was observed as its own unlabelled question. The innermost group holding every choice is it.
      if (group.length && [...el.querySelectorAll('[role=radiogroup]')].some(g => visible(g) && group.every(m => g.contains(m)))) continue;
      const nativeMember = x => x.matches('input[type=radio]') ? x :
        x.querySelectorAll('input[type=radio]').length === 1 ? x.querySelector('input[type=radio]') : null;
      // Rippling's role=radio rows hold a native radio named by aria-labelledby, not a <label>: its value ("true") was
      // read as the choice instead of "Yes - I consent to receiving text messages" (Go Fish Digital, 2026-10-27).
      const labelledBy = n => clean((n.getAttribute('aria-labelledby') || '').split(/\s+/).filter(Boolean)
        .map(ref => textOf(byId(n, ref))).join(' '));
      const optionLabel = x => {
        if (x.hasAttribute('ui5-radio-button')) return clean(x.text || x.getAttribute('text') || textOf(x));
        const native = nativeMember(x);
        return native ? clean([...(native.labels || [])].map(textOf).join(' ')) || labelledBy(native)
          || (native !== x && clean(x.innerText)) || native.value : clean(x.innerText) || label(x);
      };
      options = group.map(optionLabel);
      const chosen = group.find(x => {
        const native = nativeMember(x);
        return native ? native.checked : x.checked === true || x.getAttribute('aria-checked') === 'true';
      });
      value = chosen ? optionLabel(chosen) : '';
      // A consent group whose long caption is not its label is named by its Yes choice, as a lone radio group is.
      if ([el.id, el.getAttribute('name'), 'Unlabelled field'].includes(question)
          && /(?:text messages?|sms|marketing|newsletter)/i.test(options.join(' '))) {
        question = options.find(o => /^yes\b/i.test(o))?.replace(/^yes\s*[-–:]?\s*/i, '') || question;
      }
      // Its question is a caption inside the group, before the choices, tied to nothing ("Are you under the age of 40?").
      if (group.length && (question === 'Unlabelled field' || question === el.id || question === el.getAttribute('name'))) {
        question = precedingText(group[0], new Set(options)) || question;
      }
      // Eightfold's legend says only "Please check one of the boxes"; its options identify the disclosure.
      if (/please check one of the boxes/i.test(question)) {
        const context = options.join(' ');
        if (/veteran/i.test(context)) question = 'Veteran status: ' + question;
        else if (/disability/i.test(context)) question = 'Disability status: ' + question;
      }
    } else if (el.tagName === 'SELECT') {
      type = el.multiple ? 'select-multiple' : 'select'; options = [...el.options].filter(x => !x.disabled && !x.closest('optgroup[disabled]') && x.value !== '' && !PLACEHOLDER_VALUE.test(clean(x.text))).map(x => x.text.trim());
      value = el.multiple ? [...el.selectedOptions].map(x => x.text).join('\n') : el.selectedOptions[0]?.text || '';
      if (value && el.selectedOptions[0]?.value === '') value = '';
      if (PLACEHOLDER_VALUE.test(clean(value))) value = '';
    } else if (type === 'combobox' && !value) {
      // react-select keeps the chosen value outside the input.
      const chosen = el.closest('[class*=__control]')?.querySelectorAll('[class*=__single-value],[class*=__multi-value__label]') || [];
      value = [...chosen].map(x => clean(x.textContent)).filter(Boolean).join(', ');
      const flag = el.matches('.iti__selected-country') && [...(el.querySelector('.iti__flag')?.classList || [])]
        .map(c => /^iti__([a-z]{2})$/.exec(c)?.[1]).find(Boolean);
      if (!value && flag) {
        // intl-tel-input's country button reads "Selected country"; its flag and dial code name the choice
        // ("United States +1"). Read as empty, VAST Data's (Comeet) US phone was refused on every pass (2026-10-27).
        let region = flag.toUpperCase();
        try { region = new Intl.DisplayNames(['en'], { type: 'region' }).of(region) || region; } catch (e) {}
        value = clean(region + ' ' + textOf(el.querySelector('.iti__selected-dial-code')));
      }
      if (!value && !el.matches('input,textarea')) {
        const shown = textOf(el);
        if (!GENERIC_PLACEHOLDER.test(shown)) value = shown;
      }
    } else if (type === 'checkbox') {
      value = el.matches('input') ? (el.checked ? 'true' : 'false') : (el.getAttribute('aria-checked') === 'true' ? 'true' : 'false');
      // Dayforce ID's <evr-checkbox> keeps its state on the component, not on its shadow input.
      const evr = el.getRootNode()?.host;
      if (evr?.tagName === 'EVR-CHECKBOX') value = evr.getAttribute('checkedstate') === 'checked' ? 'true' : 'false';
    } else if (type === 'file') {
      const genericUploadQuestion = GENERIC_UPLOAD.test(question) || question === el.id || question === el.name;
      // Workday may wrap all questions and optional uploads in one formField.
      // Recover the upload's local instructions without borrowing the first
      // unrelated question's caption or required marker from that wrapper.
      if (GENERIC_UPLOAD.test(question) || question === el.id || question === el.name) {
        for (let n=el.parentElement, depth=0; n && depth<6 && ownsFile(n, el); n=n.parentElement,depth++) {
          const caption=textOf(n);
          if (caption && caption.length<=600 && /documents?|resume|\bcv\b|transcript|portfolio|cover letter|recommendation/i.test(caption)) {
            question=caption;
            break;
          }
        }
      }
      value = [...(el.files || [])].map(x => x.name).join(', ');
      // Web components clear the native input after an accepted upload and show a file list.
      const host = el.getRootNode()?.host;
      if (!value && host?.tagName === 'SPL-DROPZONE') {
        value = [...host.shadowRoot.querySelectorAll('.c-spl-file-list-item')]
          .filter(visible).map(textOf).filter(Boolean).join(', ');
      }
      const identifier = `${el.id || ''} ${el.getAttribute('name') || ''} ${el.getAttribute('data-sr-id') || ''} ${el.getAttribute('data-testid') || ''}`;
      // Greenhouse labels a hidden input only "Attach"; its upload group carries the
      // visible question ("Please upload your unofficial transcript.*") and aria-required.
      const uploadGroup = el.closest('[role=group][aria-labelledby]');
      if (uploadGroup && uploadGroup.querySelectorAll('input[type=file]').length === 1) {
        const groupLabel = clean(uploadGroup.getAttribute('aria-labelledby').split(/\s+/)
          .map(key => byId(el, key)?.textContent || '').join(' '));
        if (groupLabel && genericUploadQuestion) question = groupLabel.replace(/\s*[*✱]+\s*$/, '').trim();
        uploadRequired = uploadGroup.getAttribute('aria-required') === 'true';
      }
      // An identifier names Greenhouse's slot; a specific visible question (an employer may
      // reuse the cover-letter slot for a transcript) stays authoritative.
      if (/cover.?letter/i.test(identifier)
        && !/cover.?letter|transcript|portfolio|writing sample|work sample|other documents?|certificat|licen[cs]e/i.test(question)) question = 'Cover Letter';
      else if (!/resume|cv\b|curriculum/i.test(question)) {
        let node = el.parentElement, hint = '';
        for (let i = 0; i < 6 && node && !hint && ownsFile(node, el); i++, node = node.parentElement || node.getRootNode()?.host) {
          const t = textOf(node).slice(0, 300);
          if (/cover letter|transcript|portfolio|writing sample|other document/i.test(t)) break;
          if (/resume|\bcv\b|curriculum/i.test(t)) hint = 'Resume/CV';
        }
        if (!hint && /resume|cv/i.test((el.dataset.automationId || '') + identifier)) hint = 'Resume/CV';
        // A generic visible label ("Attach", "Upload") is replaced; a specific one is kept as context.
        if (hint) question = genericUploadQuestion && !uploadGroup ? hint :
          (GENERIC_UPLOAD.test(question) || question === el.id || question === el.name ? hint : hint + ' (' + question + ')');
      }
      // Local caption recovery can include the adjacent upload button's text.
      if (/^(?:resume(?:\s*\/\s*cv)?|cv)\s+(?:attach|upload|choose file)$/i.test(question)) question = 'Resume/CV';
    }
    if (widget === 'date-text' && dateFormat === 'month-year') {
      const picker=composedClosest(el,'spl-date-picker');
      if(picker) {
        const selected=typeof picker.value==='string'?picker.value:'';
        // A typed display string is not a committed component selection.
        value=/^\d{4}-\d{2}/.test(selected)?selected.slice(0,7):selected;
      }
    }
    // Secrets never leave the page: the model, traces and events only see that a value exists.
    // John Deere (Eightfold 2026-10-16) marks prose questions autocomplete=one-time-code to stop autofill; masking
    // them made every readback of "NA" fail. Only a code-like field is a secret.
    const codeField = /\b(?:code|otp|passcode|pin|token|verification|verify|one.?time)\b/i.test(question) ||
      (String(question || '').length <= 60 && /^[A-Za-z0-9]{4,10}$/.test(String(value || '')));
    if (type === 'password' || (el.autocomplete === 'one-time-code' && codeField && !/phone|mobile|telephone|city|postal|zip|address|name|email|country/i.test(question))) value = value ? '********' : '';
    // JazzHR: "Willing to relocate*" labels a hidden input; the real checkbox's own label is only
    // "Yes". A bare yes/agree caption takes the question (and its required star) from its container.
    let questionRequired = false;
    if (type === 'checkbox' && /^(?:yes|no|i agree|agree|true)$/i.test(clean(question || ''))) {
      const box = el.closest('div, fieldset, li');
      const qlabel = box && [...box.querySelectorAll('label, legend')]
        .find(l => !l.contains(el) && clean(l.textContent).length > 3);
      if (qlabel) {
        const t = clean(qlabel.textContent);
        questionRequired = /[*✱]/.test(t);
        question = t.replace(/\s*[*✱]+\s*$/, '').trim();
      }
    }
    const req = isReq(el, question) || uploadRequired || questionRequired || (group && group.some(x => x.required || x.getAttribute('aria-required') === 'true'));
    push(el, { label: question, type, required: req, options, value, widget, date_format: dateFormat, parts:dateParts, members:['radio','radiogroup'].includes(type) ? group : [],
      valid: (el.validity ? el.validity.valid : true) && el.getAttribute('aria-invalid') !== 'true' });
  }

  // Some portals create the native file input only after a visible upload
  // tile is clicked. Expose the explicit resume action as a file chooser,
  // without treating arbitrary Attach buttons as a resume upload.
  if (!fields.some(f => f.type === 'file' && /resume|\bcv\b|curriculum/i.test(f.label))) {
    const resumeUploadLabel = el => {
      const explicit = /\b(?:upload|attach|add)(?: your| a)? (?:resume|cv)\b/i;
      const associated = clean(el.getAttribute('aria-label') ||
        (el.getAttribute('aria-labelledby') || '').split(/\s+/).map(key=>textOf(byId(el,key))).join(' '));
      if (explicit.test(associated)) return associated;
      let q = clean(textOf(el) || el.getAttribute('aria-label') || '');
      if (explicit.test(q)) return q;
      // An icon button may be labelled by its individual upload tile.
      // A whole multi-upload section cannot supply that ownership.
      if (/[a-z]{2}/i.test(q)) return '';
      let parent = el.parentElement;
      for(let depth=0;depth<5 && parent;depth++,parent=parent.parentElement) {
        q=clean(textOf(parent));
        const candidates=[...parent.querySelectorAll('button,[role=button],[onclick],[tabindex]:not([tabindex="-1"])')].filter(visible);
        const controls=candidates.filter(node=>!candidates.some(other=>other!==node && node.contains(other)));
        if(q.length<=160 && explicit.test(q) && !/cover letter|transcript|portfolio/i.test(q)
          && controls.length===1 && controls[0]===el) return q;
      }
      return '';
    };
    const uploads = all.filter(el => el.matches('button,[role=button],[onclick],[tabindex]:not([tabindex="-1"])')
      && visible(el) && !excluded(el) && !el.disabled
      && resumeUploadLabel(el) && !/cover letter|transcript|portfolio/i.test(resumeUploadLabel(el)));
    const specific = uploads.filter(el => !uploads.some(other => other !== el && el.contains(other)));
    if (specific.length === 1) {
      const el = specific[0], q = resumeUploadLabel(el);
      push(el, {label:'Resume',type:'file',required:isReq(el,q),value:'',widget:'filechooser'});
      consumed.add(el);
    }
  }

  // A separate calling-code picker owns the prefix. Do not write it twice
  // into the one national-number field. Ambiguous multi-phone forms keep
  // their existing formatting until the association is actually observed.
  const callingCodes = fields.filter(f => /\bcountry(?:\s*(?:\/|or)\s*(?:region|territory))?(?: phone)? code\b|\bphone country\b|\bdial(?:ing)? code\b|\bcalling code\b/i.test(f.label)
    && (/\+\s*\d/.test(f.value) || f.options.some(o => /\+\s*\d/.test(o))));
  const phoneNumbers = fields.filter(f => (/phone|telephone|mobile/i.test(f.label)
      || /^number\b.*###-###-####/i.test(f.label))
    && !/code|consent|messages?|sms|type|\bext(?:ension)?\b/i.test(f.label) && ['text','tel','number'].includes(f.type));
  if(callingCodes.length === 1 && phoneNumbers.length === 1) {
    phoneNumbers[0].phone_format = 'national';
    phoneNumbers[0].phone_calling_code = callingCodes[0].value.match(/\+\s*\d{1,4}\b/)?.[0] || '';
    if (/###-###-####/.test(phoneNumbers[0].label)) phoneNumbers[0].phone_display = 'us-hyphenated';
  }

  // Resolve conditional labels in document order across different widget scans.
  for (const f of fields) {
    // Node.Digital (Workable) 2026-10-27: "If you answered \"yes\" above, please list" had no parent and was asked.
    if (!/^(?:\(?[a-z0-9]\)\W*)?if (?:yes|no|so|you (?:selected|currently|have|possess|answered)|(?:the|your) answer (?:is|was))\b/i.test(f.label)) continue;
    const node=document.querySelector('[data-jp-id="'+f.id+'"]');
    if (!node) continue;
    const prior=fields.filter(p=>p!==f && !['password','file'].includes(p.type) && !/code|password|one.time/i.test(p.label))
      .map(p=>({field:p,node:document.querySelector('[data-jp-id="'+p.id+'"]')}))
      .filter(p=>p.node && (node.compareDocumentPosition(p.node)&Node.DOCUMENT_POSITION_PRECEDING))
      .sort((a,b)=>(a.node.compareDocumentPosition(b.node)&Node.DOCUMENT_POSITION_FOLLOWING)?-1:1).at(-1);
    if(prior) f.related_question={label:prior.field.label,value:prior.field.value,options:prior.field.options};
  }
  const fieldIds = new Set(fields.map(f => f.id));
  const historyEditor = el => el.matches('[data-profile-item-tile="true"][tabindex]')
    && ['group','button'].includes(el.getAttribute('role')) && el.tabIndex >= 0
    && /a11y\.keysHandler/.test(el.getAttribute('data-bind') || '')
    && /keys:\s*['"]enter['"]/.test(el.getAttribute('data-bind') || '');
  const buttons = all.filter(el => el.matches('button,a,[role=button],input[type=submit],input[type=button],sdf-button') || historyEditor(el))
    // ADP labels live in the web-component's light DOM, while its native
    // shadow button is blank. Expose the owner once, with its actual label.
    .filter(el => el.matches('sdf-button') || !composedClosest(el,'sdf-button'))
    .filter(el => !consumed.has(el) && !fieldIds.has(el.dataset.jpId) && visible(el) && !el.disabled
      && !composedClosest(el,'[data-automation-id="languageSelectorButton"],[data-automation-id="settingsSelectorButton"]')
      // A typed date box's calendar popup (Prev / Next / 1..31) is not page navigation: left open after Axtria's
      // JazzHR mm/yyyy date, its "Next" was taken for the application's Next 45 times (2026-10-27).
      && !composedClosest(el, CALENDAR_POPUP)
      && !(el.matches('sdf-button') && ((el.shadowRoot?.querySelector('button') || el.querySelector('button'))?.disabled
        || el.hasAttribute('disabled') && el.getAttribute('disabled') !== 'false'))
      && !el.closest('[inert],[aria-hidden="true"]')
      // Controls inside a CAPTCHA widget are not navigation. Invisible reCAPTCHA
      // instead marks the site's own action button (ADP: <sdf-button class=
      // "g-recaptcha" aria-label="Continue">); that button is the only way forward.
      && !(el.parentElement || el.getRootNode()?.host)?.closest?.(CAPTCHA_BOX)
      && el.getAttribute('aria-disabled') !== 'true' && !el.matches('[aria-haspopup=listbox]'))
    .map(el => ({
      id: id(el),
      label: historyEditor(el) ? 'Select history row to reveal Edit: ' + clean(textOf(el)).slice(0,140) : clean(textOf(el) || (el.matches('sdf-button') && (textOf(el.shadowRoot?.querySelector('button') || el.querySelector('button')) || clean(el.textContent)))
        || el.value || el.getAttribute('aria-label') || el.getAttribute('title')
        // Oracle's refused submit left its tax-credit launcher without text (Mortenson, 2026-10-27): its block names it.
        || (composedClosest(el, '.inline-partner') && clean(composedClosest(el, '[role=region]')?.getAttribute('aria-label') || ''))
        || ''),
      // Oracle's inline partner launcher ("Take Tax Credit Assessment") is shown until its assessment is completed.
      partner: el.matches('[class*="partner-button"]') && !!composedClosest(el, '.inline-partner'),
      href: el.getAttribute('href') || '', type: historyEditor(el) ? 'history_select' : el.form ? (el.type || '') : '',
      automation_id: el.dataset.automationId || '', aria_label: el.getAttribute('aria-label') || '',
      html_id: el.id || '', section: section(el), group: groupOf(el),
      // A progress-step item ("Candidate Info / Questionnaire / Submit") is not an action button.
      stepper: !!el.closest('.ant-steps,[role=tablist]'),
    })).filter(x => x.label);
  // Disabled actions are not controls, but a disabled Submit says the page is still incomplete.
  const disabledActions = all.filter(el => el.matches('button,input[type=submit],[role=button]') && visible(el)
      && (el.disabled || el.getAttribute('aria-disabled') === 'true') && !el.closest(CAPTCHA_BOX))
    .map(el => clean(textOf(el) || el.value || el.getAttribute('aria-label') || '')).filter(Boolean).slice(0, 20);
  const buttonSlots={};
  for(const b of buttons) {
    const key=JSON.stringify([b.label,b.section,b.group]);
    b.slot=buttonSlots[key]||0;buttonSlots[key]=b.slot+1;
  }
  // The start (headings, questions) and the end (confirmations, footers' errors) of long pages.
  const bodyText = () => {
    const t = document.body ? document.body.innerText : '';
    return t.length > 20000 ? t.slice(0, 15000) + '\n' + t.slice(-5000) : t;
  };
  const headings = [...document.querySelectorAll('h1,h2,h3,[role=heading]')].filter(visible)
    .map(x => clean(x.innerText)).filter(Boolean).slice(0, 15);
  const errors = [...new Set([...all.filter(x=>x.matches(ERR + ',[data-automation-id="errorBanner"]'))
    .filter(visible).map(x => textOf(x).slice(0, 300)).filter(Boolean),...fields.map(f=>f.error).filter(Boolean)])].slice(0, 20);
  const automation = [...new Set([...document.querySelectorAll('[data-automation-id]')].filter(visible)
    .map(x => x.dataset.automationId))].slice(0, 400);
  const busy = [...document.querySelectorAll('[aria-busy=true],[data-automation-id="loadingSpinner"],.oj-progress-circle,.spinner,.loading-spinner,.loader')]
    .some(visible);
  // Open modal dialogs (Workday "Reset Password", terms pop-ups): their controls take precedence.
  const dialogs = [...document.querySelectorAll('[role=dialog],[role=alertdialog],[aria-modal=true],dialog[open],[data-automation-id="popUpDialog"],[data-automation-id="wd-Popup"]')]
    .filter(d => visible(d) && !d.closest(CAPTCHA_BOX) && d.getBoundingClientRect().width > 120)
    .map(d => ({
      title: clean(d.querySelector('h1,h2,h3,[role=heading]')?.innerText || d.getAttribute('aria-label') || ''),
      text: clean(d.innerText).slice(0, 4000),
      automation: [...new Set([...d.querySelectorAll('[data-automation-id]')].filter(visible).map(x => x.dataset.automationId))].slice(0, 100),
      field_ids: fields.filter(f => { const n = document.querySelector('[data-jp-id="' + f.id + '"]'); return n && d.contains(n); }).map(f => f.id),
      control_ids: buttons.filter(b => { const n = document.querySelector('[data-jp-id="' + b.id + '"]'); return n && d.contains(n); }).map(b => b.id),
    }))
    .filter(d => d.field_ids.length || d.control_ids.length);
  return {
    dialogs,
    fields, buttons, document_id: W.__jpDocId, text: bodyText(),
    title: document.title, headings, errors, automation, busy, url: location.href,
    disabled_actions: disabledActions,
  };
}
