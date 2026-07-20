/*
 * SMS-2 IDOS — FRONTEND BOOT REGRESSION TEST (Fail Safe Law)
 * ==========================================================
 * `node --check` only PARSES app.js — it can never catch execution-order
 * failures like the temporal-dead-zone bug ("Cannot access 'SFX' before
 * initialization") that blanked the login page. This harness EXECUTES the
 * whole bundle inside a minimal stub DOM and fails the build if:
 *
 *   1. any top-level statement throws, or
 *   2. boot() does not reveal the login view.
 *
 * Run:  node tests/frontend_boot_test.js
 */
"use strict";
const fs = require("fs");
const path = require("path");
const vm = require("vm");

const APP = path.join(__dirname, "..", "..", "frontend", "app.js");
const src = fs.readFileSync(APP, "utf8");

let loginRevealed = false;

function makeEl(id) {
  const el = {
    id, children: [], dataset: {}, style: {}, value: "", textContent: "",
    innerHTML: "", disabled: false, isConnected: true, offsetWidth: 0,
    classList: {
      _s: new Set(["hidden"]),
      add(...c) { c.forEach(x => this._s.add(x)); },
      remove(...c) {
        c.forEach(x => this._s.delete(x));
        if (id === "login-view" && c.includes("hidden")) loginRevealed = true;
      },
      toggle(c) { this._s.has(c) ? this._s.delete(c) : this._s.add(c); },
      contains(c) { return this._s.has(c); },
    },
    addEventListener() {}, removeEventListener() {},
    appendChild(c) { this.children.push(c); return c; },
    remove() {}, focus() {}, click() {}, closest() { return null; },
    querySelector(s) { return getEl(s); },
    querySelectorAll() { return []; },
    getAttribute() { return null; }, setAttribute() {},
    contains() { return false; },
    play() { return { catch() {} }; }, pause() {},
  };
  return el;
}

const els = new Map();
function getEl(sel) {
  if (!sel) return null;
  const key = String(sel).replace(/^#/, "");
  if (!els.has(key)) els.set(key, makeEl(key));
  return els.get(key);
}

const documentStub = {
  body: Object.assign(makeEl("body"), {dataset: {}}),
  documentElement: makeEl("html"),
  hidden: false, activeElement: null,
  getElementById: id => getEl(id),
  querySelector: sel => getEl(sel),
  querySelectorAll: () => [],
  createElement: tag => makeEl("el-" + tag),
  addEventListener() {}, removeEventListener() {},
};
documentStub.body.appendChild = c => c;

const storageStub = {
  _m: new Map(),
  getItem(k) { return this._m.has(k) ? this._m.get(k) : null; },
  setItem(k, v) { this._m.set(k, String(v)); },
  removeItem(k) { this._m.delete(k); },
  key(i) { return [...this._m.keys()][i] ?? null; },
  get length() { return this._m.size; },
};

const sandbox = {
  document: documentStub,
  localStorage: storageStub,
  location: {hash: "", href: "http://plant/", reload() {}},
  history: {pushState() {}, replaceState() {}},
  navigator: {clipboard: {writeText() { return Promise.resolve(); }}},
  fetch: () => new Promise(() => {}),            // network never resolves in the stub
  setInterval, clearInterval, setTimeout, clearTimeout,
  console,
  Audio: function () { return makeEl("audio"); },
  Notification: undefined,
  matchMedia: () => ({matches: false, addEventListener() {}}),
  requestAnimationFrame: fn => setTimeout(fn, 0),
  URLSearchParams, JSON, Object, Array, Math, Date, Promise, Set, Map,
  isNaN, parseFloat, parseInt, String, Number, Boolean, RegExp, Error, TypeError,
  encodeURIComponent, decodeURIComponent,
};
sandbox.window = sandbox;
sandbox.globalThis = sandbox;
sandbox.addEventListener = () => {};
sandbox.removeEventListener = () => {};

try {
  vm.runInNewContext(src, vm.createContext(sandbox), {filename: "app.js", timeout: 10000});
} catch (e) {
  console.error("FRONTEND BOOT TEST FAILED — top-level exception while executing app.js:");
  console.error("  " + e.name + ": " + e.message);
  process.exit(1);
}

setTimeout(() => {
  if (!loginRevealed) {
    console.error("FRONTEND BOOT TEST FAILED — app.js executed but never revealed #login-view.");
    process.exit(1);
  }
  console.log("FRONTEND BOOT TEST PASSED — app.js executes top-to-bottom, login view revealed.");
  process.exit(0);
}, 300);
