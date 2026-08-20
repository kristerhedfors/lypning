'use strict';

// Finding the addon, and failing usefully when it is not there.
//
// The invariant: this file NEVER builds anything and never asks npm to. There
// is no install script here, because a `postinstall` that shells out to cargo
// runs on someone's laptop, in their CI, behind a firewall, on a machine that
// may have no Rust toolchain at all — and it runs there because they typed
// `npm install something-else`. So the addon is built when a human asks, and
// the one thing this file owes them is an error that says exactly how.
//
// $LYPNING_NODE_ADDON is "I know where it is, use that one" — an instruction,
// so when it is set it is the only candidate and a bad value is an error, never
// a fallback onto some other addon the user did not name. With it unset there
// are two places, and they answer two different questions: ~/.lypning/lib is
// where `lypning build` installs it, which is the wheel-install case where this
// directory is read-only; the crate's own target/ is the source checkout, where
// `cargo build --release` here shares an object cache with the rest of the
// tree. A checkout that has both prefers the deliberate one over the
// incidental one.

const fs = require('fs');
const os = require('os');
const path = require('path');

const MANIFEST = path.join(__dirname, 'Cargo.toml');
const BUILD_COMMAND = 'cargo build --release --manifest-path ' + MANIFEST;

// The cdylib's name as each platform's linker spells it. Not a lookup of what
// node calls an addon: process.dlopen does not care about the extension, and
// renaming the artifact to `.node` would be a build step this crate does not
// have.
function cdylibNames() {
  if (process.platform === 'win32') return ['lypning_node.dll'];
  if (process.platform === 'darwin') return ['liblypning_node.dylib'];
  return ['liblypning_node.so'];
}

function candidates() {
  const out = [];
  const home = process.env.LYPNING_HOME || path.join(os.homedir(), '.lypning');
  out.push(path.join(home, 'lib', 'lypning.node'));
  for (const name of cdylibNames()) out.push(path.join(home, 'lib', name));

  // `release-lib` is the profile the rest of the tree builds the library with;
  // it is checked so a developer who used it out of habit is not told the
  // addon is missing while it sits one directory over.
  for (const profile of ['release', 'release-lib', 'debug']) {
    for (const name of cdylibNames()) {
      out.push(path.join(__dirname, 'target', profile, name));
    }
  }
  return out;
}

function isFile(p) {
  try {
    return fs.statSync(p).isFile();
  } catch (e) {
    // Unreadable is indistinguishable from absent here, and for the search
    // below both mean "keep looking".
    return false;
  }
}

function locate() {
  // $LYPNING_NODE_ADDON is not a hint, it is an instruction — so it is the ONLY
  // candidate when it is set, and a typo in it is an error rather than a search.
  // Falling through to the next candidate would load a DIFFERENT addon than the
  // one the user named, most likely the stale copy in ~/.lypning/lib they set
  // the variable to bypass, and report a version they did not ask for. Failing
  // here costs them one clear message; succeeding quietly costs them the
  // afternoon.
  const explicit = process.env.LYPNING_NODE_ADDON;
  if (explicit) {
    if (isFile(explicit)) return explicit;
    throw new Error(
      'lypning: $LYPNING_NODE_ADDON is set but is not a readable file:\n' +
        '\n' +
        '      ' + explicit + '\n' +
        '\n' +
        '  Unset it to search the usual places, or point it at a built addon.\n' +
        '  Build one with:\n' +
        '      ' + BUILD_COMMAND + '\n'
    );
  }

  const tried = candidates();
  for (const p of tried) {
    // The per-path failure is swallowed on purpose: the error a caller needs is
    // the summary below, not five ENOENTs.
    if (isFile(p)) return p;
  }
  throw new Error(
    'lypning: the Node addon is not built.\n' +
      '\n' +
      '  Build it with:\n' +
      '      ' + BUILD_COMMAND + '\n' +
      '  or set $LYPNING_NODE_ADDON to a built addon.\n' +
      '\n' +
      '  Looked in:\n' +
      tried.map(function (p) { return '      ' + p; }).join('\n') + '\n'
  );
}

const addonPath = locate();

// process.dlopen, not require(). require() would insist on a `.node`
// extension and go through the module cache; dlopen takes the artifact cargo
// produced, under the name cargo produced it, and hands us its exports. The
// napi_register_module_v1 symbol in src/lib.rs is what makes that work — node
// looks it up by name in the freshly loaded object.
const addon = { exports: {} };
process.dlopen(addon, addonPath);

module.exports = addon.exports;

// Which file answered. Diagnostic only: a harness that gets a surprising
// version wants to know which of the three candidates it loaded.
module.exports.addonPath = addonPath;
