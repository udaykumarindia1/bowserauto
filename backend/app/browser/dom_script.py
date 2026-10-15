"""JavaScript injected into pages to enumerate interactive elements.

The script tags every visible interactive element with an index, stores the element
references on ``window.__ba_elements`` (so later actions can resolve ``{"index": n}``) and
returns a JSON-serialisable description that the agent can reason about.
"""

INTERACTIVE_ELEMENTS_SCRIPT = r"""
(() => {
  const INTERACTIVE = 'a[href], button, input, textarea, select, summary, [role="button"], [role="link"], [role="textbox"], [role="checkbox"], [role="radio"], [role="tab"], [role="menuitem"], [role="option"], [role="combobox"], [role="switch"], [role="slider"], [role="listbox"], [contenteditable="true"], [contenteditable=""], [onclick], [tabindex]:not([tabindex="-1"])';
  const vw = window.innerWidth, vh = window.innerHeight;
  const seen = new Set();
  const results = [];
  const elements = [];
  const isVisible = (el) => {
    const style = window.getComputedStyle(el);
    if (style.display === 'none' || style.visibility === 'hidden' || parseFloat(style.opacity || '1') < 0.05) return false;
    const rect = el.getBoundingClientRect();
    if (rect.width < 2 || rect.height < 2) return false;
    if (rect.bottom < -200 || rect.top > vh + 400 || rect.right < 0 || rect.left > vw) return false;
    return true;
  };
  const text = (el) => (el.innerText || el.textContent || '').replace(/\s+/g, ' ').trim();
  const accessibleName = (el) => {
    const aria = el.getAttribute('aria-label');
    if (aria) return aria.trim();
    const labelledBy = el.getAttribute('aria-labelledby');
    if (labelledBy) {
      const parts = labelledBy.split(/\s+/).map(id => document.getElementById(id)).filter(Boolean).map(text);
      if (parts.length) return parts.join(' ');
    }
    if (el.id) {
      const label = document.querySelector(`label[for="${CSS.escape(el.id)}"]`);
      if (label) return text(label);
    }
    const parentLabel = el.closest('label');
    if (parentLabel && parentLabel !== el) return text(parentLabel).slice(0, 120);
    const title = el.getAttribute('title');
    if (title) return title.trim();
    const img = el.querySelector('img[alt]');
    if (img && img.getAttribute('alt')) return img.getAttribute('alt').trim();
    const svgTitle = el.querySelector('svg title');
    if (svgTitle) return text(svgTitle);
    if (el.tagName === 'INPUT' && (el.type === 'submit' || el.type === 'button')) return el.value || '';
    const t = text(el);
    return t.slice(0, 160);
  };
  const cssPath = (el) => {
    if (el.id && !/\d{3,}/.test(el.id)) return '#' + CSS.escape(el.id);
    const testid = el.getAttribute('data-testid');
    if (testid) return `[data-testid="${testid}"]`;
    const parts = [];
    let node = el;
    while (node && node.nodeType === 1 && parts.length < 6) {
      let part = node.tagName.toLowerCase();
      if (node.id && !/\d{3,}/.test(node.id)) { parts.unshift('#' + CSS.escape(node.id)); break; }
      const parent = node.parentElement;
      if (parent) {
        const siblings = Array.from(parent.children).filter(c => c.tagName === node.tagName);
        if (siblings.length > 1) part += `:nth-of-type(${siblings.indexOf(node) + 1})`;
      }
      parts.unshift(part);
      node = parent;
    }
    return parts.join(' > ');
  };
  const roleOf = (el) => {
    const explicit = el.getAttribute('role');
    if (explicit) return explicit;
    const tag = el.tagName.toLowerCase();
    if (tag === 'a') return 'link';
    if (tag === 'button' || tag === 'summary') return 'button';
    if (tag === 'select') return 'combobox';
    if (tag === 'textarea') return 'textbox';
    if (tag === 'input') {
      const type = (el.getAttribute('type') || 'text').toLowerCase();
      if (['button', 'submit', 'reset', 'image'].includes(type)) return 'button';
      if (type === 'checkbox') return 'checkbox';
      if (type === 'radio') return 'radio';
      if (type === 'file') return 'file';
      if (type === 'range') return 'slider';
      return 'textbox';
    }
    if (el.isContentEditable) return 'textbox';
    return 'generic';
  };
  const all = Array.from(document.querySelectorAll(INTERACTIVE));
  // Also include shadow roots one level deep (common in modern web apps).
  document.querySelectorAll('*').forEach(host => {
    if (host.shadowRoot) { all.push(...Array.from(host.shadowRoot.querySelectorAll(INTERACTIVE))); }
  });
  let index = 0;
  for (const el of all) {
    if (seen.has(el)) continue;
    seen.add(el);
    if (!isVisible(el)) continue;
    // Skip wrappers whose only purpose is to contain another interactive element.
    const rect = el.getBoundingClientRect();
    const tag = el.tagName.toLowerCase();
    const role = roleOf(el);
    const name = accessibleName(el);
    const info = {
      index,
      tag,
      role,
      name: name.slice(0, 200),
      text: text(el).slice(0, 200),
      placeholder: (el.getAttribute('placeholder') || '').slice(0, 120),
      value: (tag === 'input' || tag === 'textarea') ? String(el.value || '').slice(0, 200) : (el.isContentEditable ? text(el).slice(0, 200) : ''),
      href: tag === 'a' ? (el.getAttribute('href') || '').slice(0, 300) : '',
      input_type: tag === 'input' ? (el.getAttribute('type') || 'text').toLowerCase() : '',
      disabled: !!(el.disabled || el.getAttribute('aria-disabled') === 'true'),
      checked: (tag === 'input' && (el.type === 'checkbox' || el.type === 'radio')) ? !!el.checked : (el.getAttribute('aria-checked') ? el.getAttribute('aria-checked') === 'true' : null),
      bbox: [Math.round(rect.x), Math.round(rect.y), Math.round(rect.width), Math.round(rect.height)],
      selector: cssPath(el),
    };
    results.push(info);
    elements.push(el);
    index += 1;
    if (index >= 400) break;
  }
  window.__ba_elements = elements;
  const fileInputs = document.querySelectorAll('input[type="file"]').length;
  const body = document.body ? (document.body.innerText || '') : '';
  return {
    url: location.href,
    title: document.title,
    text: body.replace(/\n{3,}/g, '\n\n').slice(0, 20000),
    elements: results,
    scroll: [Math.round(window.scrollY), vh, Math.round(document.documentElement.scrollHeight)],
    file_inputs: fileInputs,
    loading: document.readyState !== 'complete',
  };
})()
"""

ELEMENT_BY_INDEX_SCRIPT = "(i) => (window.__ba_elements && window.__ba_elements[i]) || null"

PAGE_TEXT_SCRIPT = "(max) => (document.body ? document.body.innerText : '').replace(/\\n{3,}/g, '\\n\\n').slice(0, max)"
