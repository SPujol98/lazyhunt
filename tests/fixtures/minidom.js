'use strict';

// Minimal DOM for the portal fixture tests (no jsdom in the repo's test deps).
// Parses well-formed synthetic HTML and implements only what the portal
// extractors use: querySelector(All) / closest over compound selectors
// (tag, .class, #id, [attr], [attr=v], [attr^=v], [attr*=v], each optionally ` i`), getAttribute,
// parentElement, textContent and innerText. `textContent` concatenates every
// text node of the subtree, as in a browser. `innerText` is simplified: the trimmed,
// non-empty text nodes of the subtree joined by "\n" (one line per text
// node), which is how the saved snippets model the cards' visible lines.

const VOID = new Set(['br', 'img', 'input', 'meta', 'link', 'hr']);

class El {
  constructor(tag, attrs, parent) {
    this.tag = tag;
    this.attrs = attrs;
    this.parentElement = parent;
    this.children = []; // El | string
  }

  getAttribute(name) {
    return name in this.attrs ? this.attrs[name] : null;
  }

  get innerText() {
    const lines = [];
    const walk = (node) => {
      for (const child of node.children) {
        if (typeof child === 'string') {
          const t = child.trim();
          if (t) lines.push(t);
        } else walk(child);
      }
    };
    walk(this);
    return lines.join('\n');
  }

  get textContent() {
    const parts = [];
    const walk = (node) => {
      for (const child of node.children) {
        if (typeof child === 'string') parts.push(child);
        else walk(child);
      }
    };
    walk(this);
    return parts.join('');
  }

  scrollTo() {}

  matches(selector) {
    return matchesCompound(this, selector);
  }

  closest(selector) {
    for (let el = this; el; el = el.parentElement) {
      if (el.tag !== '#root' && el.matches(selector)) return el;
    }
    return null;
  }

  querySelectorAll(selector) {
    const out = [];
    const walk = (node) => {
      for (const child of node.children) {
        if (typeof child === 'string') continue;
        if (child.matches(selector)) out.push(child);
        walk(child);
      }
    };
    walk(this);
    return out;
  }

  querySelector(selector) {
    return this.querySelectorAll(selector)[0] || null;
  }
}

function matchesCompound(el, selector) {
  const tag = (selector.match(/^[a-zA-Z][a-zA-Z0-9]*/) || [null])[0];
  if (tag && el.tag !== tag.toLowerCase()) return false;
  const rest = tag ? selector.slice(tag.length) : selector;
  const idm = rest.replace(/\[[^\]]*\]/g, '').match(/#([\w-]+)/);
  if (idm && el.attrs.id !== idm[1]) return false;
  const classes = [...rest.replace(/\[[^\]]*\]/g, '').matchAll(/\.([\w-]+)/g)].map((m) => m[1]);
  const have = (el.attrs.class || '').split(/\s+/);
  if (!classes.every((c) => have.includes(c))) return false;
  for (const m of rest.matchAll(/\[([\w-]+)(?:([\^*]?=)"([^"]*)"( i)?)?\]/g)) {
    const [, name, op, rawValue, ci] = m;
    let actual = el.getAttribute(name);
    if (actual === null) return false;
    let value = rawValue;
    if (ci) {
      actual = actual.toLowerCase();
      value = value.toLowerCase();
    }
    if (op === '=' && actual !== value) return false;
    if (op === '^=' && !actual.startsWith(value)) return false;
    if (op === '*=' && !actual.includes(value)) return false;
  }
  return true;
}

function parseHtml(html) {
  const root = new El('#root', {}, null);
  let cur = root;
  const tokens = html.replace(/<!--[\s\S]*?-->/g, '').matchAll(/<\/?[a-zA-Z][^>]*>|[^<]+/g);
  for (const [tok] of tokens) {
    if (tok.startsWith('</')) {
      cur = cur.parentElement || root;
    } else if (tok.startsWith('<')) {
      const m = tok.match(/^<([a-zA-Z][a-zA-Z0-9]*)([\s\S]*?)(\/?)>$/);
      const attrs = {};
      for (const a of m[2].matchAll(/([\w-]+)(?:="([^"]*)")?/g)) attrs[a[1]] = a[2] ?? '';
      const el = new El(m[1].toLowerCase(), attrs, cur);
      cur.children.push(el);
      if (!VOID.has(el.tag) && !m[3]) cur = el;
    } else {
      cur.children.push(tok);
    }
  }
  return root;
}

// Installs `document`/`window` globals over the parsed snippet.
function installDocument(html) {
  const root = parseHtml(html);
  const body = new El('body', {}, root);
  body.children = root.children;
  for (const c of body.children) if (typeof c !== 'string') c.parentElement = body;
  globalThis.document = {
    body,
    title: '',
    querySelector: (s) => body.querySelector(s),
    querySelectorAll: (s) => body.querySelectorAll(s),
  };
  globalThis.window = { scrollTo() {} };
}

function uninstallDocument() {
  delete globalThis.document;
  delete globalThis.window;
}

module.exports = { parseHtml, installDocument, uninstallDocument };
