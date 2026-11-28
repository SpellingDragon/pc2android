'use strict';

const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');

const source = fs.readFileSync(path.join(__dirname, 'pc2android.py'), 'utf8');
const match = source.match(/VIRTUAL_KEYS_TEMPLATE = r'''([\s\S]*?)'''/);
assert.ok(match, 'virtual key template not found');

const key = (id, label, keyCode, code, value, group = 'extra') => ({
  id, label, keyCode, code, key: value, group
});
const profile = {
  keys: [
    key('up', '↑', 38, 'ArrowUp', 'ArrowUp', 'dpad'),
    key('down', '↓', 40, 'ArrowDown', 'ArrowDown', 'dpad'),
    key('left', '←', 37, 'ArrowLeft', 'ArrowLeft', 'dpad'),
    key('right', '→', 39, 'ArrowRight', 'ArrowRight', 'dpad'),
    key('ok', 'OK', 13, 'Enter', 'Enter', 'action'),
    key('cancel', 'ESC', 27, 'Escape', 'Escape', 'action'),
    key('shift', 'DASH', 16, 'ShiftLeft', 'Shift', 'action'),
    key('tab', 'SKIP', 9, 'Tab', 'Tab')
  ]
};
const script = match[1].replace('__PROFILE_JSON__', JSON.stringify(profile));

class FakeEvent {
  constructor(type, options = {}) {
    this.type = type;
    this.bubbles = Boolean(options.bubbles);
    this.cancelable = Boolean(options.cancelable);
    this.defaultPrevented = false;
    this._stopped = false;
    this.target = null;
  }
  preventDefault() { if (this.cancelable) this.defaultPrevented = true; }
  stopPropagation() { this._stopped = true; }
}

class FakeKeyboardEvent extends FakeEvent {
  constructor(type, options = {}) {
    super(type, options);
    this.key = options.key || '';
    this.code = options.code || '';
    this.keyCode = options.keyCode || 0;
    this.which = options.which || 0;
    this.charCode = options.charCode || 0;
  }
}

class FakePointerEvent extends FakeEvent {
  constructor(type, options = {}) {
    super(type, options);
    this.pointerId = options.pointerId || 0;
    this.pointerType = options.pointerType || '';
  }
}

class FakeTouchEvent extends FakeEvent {
  constructor(type, options = {}) {
    super(type, options);
    this.touches = options.touches || [];
    this.targetTouches = options.targetTouches || [];
    this.changedTouches = options.changedTouches || [];
  }
}

class FakeEventTarget {
  constructor() { this.listeners = new Map(); this.parentNode = null; }
  addEventListener(type, listener, options = {}) {
    const capture = options === true || Boolean(options && options.capture);
    const entries = this.listeners.get(type) || [];
    entries.push({ listener, capture });
    this.listeners.set(type, entries);
  }
  dispatchEvent(event) {
    const ancestors = [];
    for (let node = this; node; node = node.parentNode) ancestors.push(node);
    event.target = this;
    for (let index = ancestors.length - 1; index >= 0 && !event._stopped; index--) {
      this.notify(ancestors[index], event, true);
    }
    if (!event._stopped) this.notify(this, event, false);
    for (let index = 1; index < ancestors.length && !event._stopped; index++) {
      this.notify(ancestors[index], event, false);
    }
    return !event.defaultPrevented;
  }
  notify(node, event, capture) {
    for (const entry of node.listeners.get(event.type) || []) {
      if (entry.capture === capture) entry.listener.call(node, event);
      if (event._stopped) break;
    }
  }
}

class FakeClassList {
  constructor(element) { this.element = element; }
  values() { return this.element.className.split(/\s+/).filter(Boolean); }
  contains(value) { return this.values().includes(value); }
  add(value) { if (!this.contains(value)) this.element.className += ` ${value}`; }
  remove(value) { this.element.className = this.values().filter(item => item !== value).join(' '); }
  toggle(value) { if (this.contains(value)) this.remove(value); else this.add(value); }
}

class FakeElement extends FakeEventTarget {
  constructor(tagName) {
    super();
    this.tagName = tagName.toUpperCase();
    this.id = '';
    this.className = '';
    this.textContent = '';
    this.dataset = {};
    this.style = { setProperty(name, value) { this[name] = value; } };
    this.children = [];
    this.classList = new FakeClassList(this);
    this.value = '';
  }
  appendChild(child) { child.parentNode = this; this.children.push(child); return child; }
  append(...children) { children.forEach(child => this.appendChild(child)); }
  setPointerCapture() {}
}

class FakeDocument extends FakeEventTarget {
  constructor() {
    super();
    this.documentElement = new FakeElement('html');
    this.body = new FakeElement('body');
    this.documentElement.appendChild(this.body);
    this.body.parentNode = this;
    this.hidden = false;
    this.canvas = new FakeElement('canvas');
    this.body.appendChild(this.canvas);
  }
  createElement(tagName) { return new FakeElement(tagName); }
  querySelector(selector) { return selector === 'canvas' ? this.canvas : null; }
}

function install() {
  const document = new FakeDocument();
  const window = new FakeEventTarget();
  const localStorage = {
    values: new Map(),
    getItem(name) { return this.values.get(name) ?? null; },
    setItem(name, value) { this.values.set(name, value); }
  };
  const context = {
    window,
    document,
    localStorage,
    KeyboardEvent: FakeKeyboardEvent,
    PointerEvent: FakePointerEvent,
    TouchEvent: FakeTouchEvent,
    setTimeout,
    clearTimeout,
    console
  };
  vm.runInNewContext(script, context, { filename: 'virtual-keys.js' });
  const find = (node, id) => {
    if (node.dataset && node.dataset.id === id) return node;
    for (const child of node.children || []) {
      const found = find(child, id);
      if (found) return found;
    }
    return null;
  };
  return { document, window, root: window.__pc2aVirtualKeys.root, find };
}

function pointer(element, type, pointerId) {
  element.dispatchEvent(new FakePointerEvent(type, {
    bubbles: true, cancelable: true, pointerId, pointerType: 'touch'
  }));
}

function touch(element, type, pointerId = 1) {
  const changed = [{ identifier: pointerId, target: element }];
  const active = type === 'touchend' || type === 'touchcancel' ? [] : changed;
  element.dispatchEvent(new FakeTouchEvent(type, {
    bubbles: true, cancelable: true, touches: active, targetTouches: active, changedTouches: changed
  }));
}

test('virtual controls isolate touch events from game document listeners', () => {
  const { document, find } = install();
  const seen = [];
  for (const type of ['touchstart', 'touchmove', 'touchend', 'touchcancel']) {
    document.addEventListener(type, event => seen.push({
      type,
      target: event.target.dataset.id,
      defaultPrevented: event.defaultPrevented
    }));
  }
  const skip = find(document.body, 'tab');
  for (const type of ['touchstart', 'touchmove', 'touchend', 'touchcancel']) touch(skip, type);
  assert.deepEqual(seen, [], 'overlay touches must not become RPG Maker TouchInput');
});

test('SKIP preserves keydown/keyup and pointer capture release contract', () => {
  const { document, find } = install();
  const events = [];
  for (const type of ['keydown', 'keyup']) {
    document.addEventListener(type, event => events.push([
      type, event.key, event.code, event.keyCode, event.which, event.charCode
    ]));
  }
  const skip = find(document.body, 'tab');
  touch(skip, 'touchstart');
  pointer(skip, 'pointerdown', 7);
  assert.equal(skip.classList.contains('pc2a-vk-active'), true);
  pointer(skip, 'pointerup', 7);
  pointer(skip, 'lostpointercapture', 7);
  touch(skip, 'touchend');
  assert.equal(skip.classList.contains('pc2a-vk-active'), false);
  assert.deepEqual(events, [
    ['keydown', 'Tab', 'Tab', 9, 9, 0],
    ['keyup', 'Tab', 'Tab', 9, 9, 0]
  ]);
});

test('SKIP and direction remain independent across multi-touch release', () => {
  const { document, find } = install();
  const events = [];
  for (const type of ['keydown', 'keyup']) {
    document.addEventListener(type, event => events.push([type, event.key]));
  }
  const left = find(document.body, 'left');
  const skip = find(document.body, 'tab');
  pointer(left, 'pointerdown', 1);
  pointer(skip, 'pointerdown', 2);
  pointer(skip, 'pointerup', 2);
  pointer(left, 'pointerup', 1);
  assert.deepEqual(events, [
    ['keydown', 'ArrowLeft'],
    ['keydown', 'Tab'],
    ['keyup', 'Tab'],
    ['keyup', 'ArrowLeft']
  ]);
  assert.equal(left.classList.contains('pc2a-vk-active'), false);
  assert.equal(skip.classList.contains('pc2a-vk-active'), false);
});

test('touch isolation does not cancel native click or range behavior', () => {
  const { document, find } = install();
  const skip = find(document.body, 'tab');
  const event = new FakeTouchEvent('touchstart', {
    bubbles: true, cancelable: true, touches: [{}], targetTouches: [{}], changedTouches: [{}]
  });
  skip.dispatchEvent(event);
  assert.equal(event.defaultPrevented, false);
});
