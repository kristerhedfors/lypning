//! `from __future__ import …` — the `cap-future` capability.
//!
//! A future import is a COMPILER directive, not a module import. CPython
//! decides it before a single statement runs, which is why every misuse of one
//! is a `SyntaxError` and never an `ImportError`: an unknown feature, an
//! `as`-less `braces`, or the import anywhere but the top of the module —
//! including inside a `def` that is never called. A runtime import sees none
//! of that, so this is a PASS over the parse, hooked once at the end of
//! `parse::parse()`: the walk (`route -c`) and the run see the same program.
//!
//! What the pass serves, and why it is everything that matters:
//!
//! * the leading run of `from __future__ import` statements, after at most
//!   ONE plain-`str` docstring (bytes and f-strings are not docstrings, and a
//!   second string is a statement), is REMOVED from the body;
//! * every name in it must be on [`SERVED`] and unaliased. All but one of
//!   those names do nothing at all in Python 3 — their features are mandatory.
//!   The one that does something is `annotations`, and what it does is that
//!   annotations are never evaluated, which the pass makes true by clearing
//!   every `def`'s annotation list. The engine otherwise evaluates them when
//!   the `def` runs (`ast::Params::anns`), and under the import that would
//!   raise `NameError` for an annotation naming what was never imported —
//!   `-> Optional[str]` with no `typing` — which is the common case.
//!   From 3.14 (PEP 649) CPython defers them with or without the import, so
//!   on a 3.14+ reference EVERY served head clears them; on an older one only
//!   `annotations` does. On a build whose reference minor was guessed
//!   (`err::REF_PY_KNOWN` false) the two answers cannot be told apart, so an
//!   annotated `def` under any other head refuses rather than pick one.
//!
//! What it refuses, statically and as the whole program, kind `future`:
//!
//! * a name off the list (`braces`, a misspelling, a feature from a later
//!   Python) or an alias, which CPython answers with a `SyntaxError` or, for
//!   `as`, with a `_Feature` binding this engine has no value for;
//! * `barry_as_FLUFL` as a NAME anywhere in a program that imports
//!   `__future__`, before the parse result is looked at, because it changes the grammar (`1 <> 2` is valid under it)
//!   and the parser would otherwise answer a program CPython runs with a
//!   `SyntaxError` at exit 1;
//! * any other `__future__` in the program: a misplaced import, `import
//!   __future__`, an attribute. Counted, not walked — the pass removes one
//!   `__future__` token per statement it consumed, and any left over refuses;
//! * any non-ASCII identifier or f-string text once `__future__` appears:
//!   CPython NFKC-folds identifiers (`ｄivision` is `division`) and this
//!   lexer does not, so the name counts below would miss such a use;
//! * the imported feature's own name, and `__annotations__`, anywhere else:
//!   the import binds a `_Feature` object and the annotations it defers
//!   become strings, and neither exists here. The corpus uses neither
//!   (mined 2026-09-24), so the refusal costs nothing measured;
//! * any `__debug__` (CPython rejects every binding of it).
//!
//! And it ANSWERS every compile-time `SyntaxError` CPython raises and this
//! parser does not, with that `SyntaxError`, before anything runs: whatever
//! the lexer or parser noted as lax (`parse::note_lax` — a duplicate
//! parameter, a parameter without a default after one with, `break`/
//! `continue` outside a loop, `return` outside a function, a name spelled
//! before its `global`, an unparenthesized generator beside another argument,
//! a positional argument after a keyword one, anything after `**`, `0777` or
//! a misplaced `_` in a number, a bare `except:` before another clause, an
//! f-string conversion other than `!s`/`!r`/`!a`, a second starred target).
//! The core refuses every program with a head statically, so each of these
//! went to CPython before `cap-future`, and so did every program WITHOUT a
//! head that the core routes past itself for another capability
//! ([`past_the_core`]). The same gaps in a program the core routes to itself
//! are the core's, and are left as the core answers them.
//!
//! A program that does not import `__future__` — a `__future__` NAME right
//! after `from` or `import` (`route::future_imported`) — pays one scan of its
//! tokens and is otherwise the core's: a string, a comment, an f-string's
//! text, a variable, parameter or argument named `__future__` or
//! `barry_as_FLUFL` is answered as the core answers it.

use crate::ast::*;
use crate::err::{unsupported, R};
use crate::lex::Token;
use std::rc::Rc;

/// The feature names served, which is the list `route::MODULE_ATTRS` carries
/// for `__future__` so the CORE's walk sends every other name to CPython.
pub const SERVED: &[&str] = crate::route::FUTURE_SERVED;

use crate::route::{future_head, future_imported, future_token_block};

fn refuse(detail: &str) -> crate::err::LypningError {
    unsupported("future", detail)
}

/// The pass. `body` is the parse as it came out, error included, because
/// `barry_as_FLUFL` has to be refused whether or not its grammar parsed.
pub fn pass(body: R<Vec<Stmt>>, toks: &[Token], lax: Option<(&'static str, u32)>, src: &str) -> R<Vec<Stmt>> {
    // Only a program that imports `__future__` is looked at: a variable,
    // a parameter or an argument named `__future__` or `barry_as_FLUFL` is
    // the core's program (`route::future_imported`). The grammar change can
    // only come in through that import, so `future_token_block` below still
    // refuses it whether or not its grammar parsed.
    if !future_imported(toks) {
        return past_the_core(body, toks, lax, src);
    }
    // CPython NFKC-folds every identifier, so `ｄivision` IS `division` and
    // `ｂarry_as_FLUFL` is the grammar change; see `route::future_token_block`.
    if let Some(why) = future_token_block(toks) {
        return Err(refuse(why));
    }
    let mut body = body?;
    // CPython's compiler answers these before anything runs, and the core
    // refuses every program with a head, so the answer is CPython's
    // SyntaxError (`lax_answer`).
    if let Some(l) = lax {
        return Err(lax_answer(l, "future"));
    }
    // The head's shape, the names it serves and every other spelling of them
    // — decided in `route.rs`, because the CORE's walk asks the same question
    // and routes a head this pass would refuse straight to CPython (#48).
    let (start, end, names) = future_head(&body, toks).map_err(|d| refuse(&d))?;
    let own = |n: &str| names.iter().filter(|m| m.as_ref() == n).count();
    use crate::err::{REF_PY_KNOWN, REF_PY_MINOR};
    let deferred = own("annotations") > 0 || (REF_PY_KNOWN && REF_PY_MINOR >= 14);
    body.drain(start..end);
    if deferred && !defer(&mut body, true) {
        return Err(refuse("annotations on a definition the parse shared"));
    }
    if !deferred && !REF_PY_KNOWN && !defer(&mut body, false) {
        return Err(refuse("an annotated def, with the reference Python's minor unmeasured (PEP 649)"));
    }
    Ok(body)
}

/// A lax construct's answer: CPython's `SyntaxError`, exactly in kind — exit
/// 1, nothing on stdout — though not always in wording. So a ROUTED run
/// (`route::routed`), whose chain goes on to CPython, refuses as `kind`
/// instead, and the chain prints CPython's own line; a direct run gets the
/// `SyntaxError`, which is what the core, running the same program, never
/// answers better.
fn lax_answer((why, line): (&'static str, u32), kind: &str) -> crate::err::LypningError {
    if crate::route::routed() {
        return unsupported(kind, why);
    }
    crate::err::LypningError::syntax(line, why)
}

/// A program WITHOUT a head that the core's own walk routes past the core
/// (`route::core_admits` false: an import only a capability serves, a
/// `random.sample`, a `sys.version_info`). Every one of them went to CPython
/// before its capability existed, and CPython's compiler answered what this
/// parser noted as lax with a `SyntaxError` before anything ran — so that is
/// the answer here too, exactly: exit 1, empty stdout, that exception. It is
/// never a wrong one for the core either: the core answers such a program
/// with whatever its lax parse runs to, which CPython never does.
///
/// A `__debug__` in one refuses as the core refuses reading it (`builtin`):
/// CPython rejects every binding of it at compile time, and reads it as a
/// constant this engine has no value for.
///
/// The walk is paid only by a program that has one of the two.
fn past_the_core(body: R<Vec<Stmt>>, toks: &[Token], lax: Option<(&'static str, u32)>, src: &str) -> R<Vec<Stmt>> {
    let debug = crate::route::future_names(toks, "__debug__") > 0;
    if lax.is_none() && !debug {
        return body;
    }
    let Ok(b) = &body else { return body };
    #[cfg(any(feature = "cap-itertools", feature = "cap-difflib", feature = "cap-time"))]
    if !crate::route::core_admits(b, src) {
        if debug {
            return Err(unsupported("builtin", "__debug__"));
        }
        if let Some(l) = lax {
            return Err(lax_answer(l, "syntax"));
        }
    }
    let _ = (b, src);
    body
}

/// `from __future__ import annotations`: no annotation on any `def`, at any
/// depth, is ever evaluated. A class would be a second place annotations live,
/// and classes are refused by the parser. Lambdas have none, and an annotated
/// assignment's annotation is parsed and dropped already.
///
/// `Rc::get_mut` and not `make_mut`: a fresh parse owns every node once, and
/// `make_mut` would link the whole derived `Clone` of the tree into the binary
/// to copy what is never shared. `false` if a node WAS shared, which the
/// caller refuses rather than evaluating an annotation it promised not to.
///
/// With `clear` false the same walk only LOOKS: `false` if any `def` carries
/// an annotation, which is the unmeasured-reference refusal.
fn defer(body: &mut [Stmt], clear: bool) -> bool {
    body.iter_mut().all(|s| match s {
        Stmt::Def { params, body, .. } => match (Rc::get_mut(params), Rc::get_mut(body)) {
            (Some(p), Some(b)) if clear || p.anns.is_empty() => {
                p.anns.clear();
                defer(b, clear)
            }
            _ => false,
        },
        Stmt::If { arms, els } => arms.iter_mut().all(|(_, b)| defer(b, clear)) && defer(els, clear),
        Stmt::For { body, els, .. } | Stmt::While { body, els, .. } => defer(body, clear) && defer(els, clear),
        Stmt::Try { body, handlers, els, finally } => {
            defer(body, clear)
                && handlers.iter_mut().all(|h| defer(&mut h.body, clear))
                && defer(els, clear)
                && defer(finally, clear)
        }
        Stmt::With { body, .. } => defer(body, clear),
        _ => true,
    })
}

#[cfg(test)]
mod tests {
    use super::*;

    /// `route::MODULE_ATTRS` is what the CORE routes on, and this module is
    /// what lypning-l serves: one list, and a test that says so.
    #[test]
    fn the_route_table_names_exactly_what_is_served() {
        let row = crate::route::MODULE_ATTRS
            .iter()
            .find(|(m, _)| *m == "__future__")
            .expect("route::MODULE_ATTRS has no __future__ row");
        assert_eq!(row.1, SERVED);
        let cap = crate::route::CAPS
            .iter()
            .find(|(c, _, _)| *c == "cap-future")
            .expect("route::CAPS has no cap-future row");
        assert_eq!(cap.1, &["__future__"]);
        assert!(cap.2.is_empty());
    }

    fn run(src: &str) -> R<Vec<Stmt>> {
        crate::parse::parse(src)
    }

    fn kind(src: &str) -> String {
        match run(src) {
            Err(e) => match e.kind() {
                crate::err::ErrKind::Unsupported { kind, .. } => kind.clone(),
                crate::err::ErrKind::Syntax { .. } => "SyntaxError".into(),
                _ => "error".into(),
            },
            Ok(_) => "ok".into(),
        }
    }

    #[test]
    fn the_head_is_stripped_and_annotations_are_cleared() {
        let b = run("\"\"\"d\"\"\"\nfrom __future__ import annotations\ndef f(a: X) -> Y:\n    def g(b: Z): pass\n").unwrap();
        assert_eq!(b.len(), 2);
        match &b[1] {
            Stmt::Def { params, body, .. } => {
                assert!(params.anns.is_empty());
                match &body[0] {
                    Stmt::Def { params, .. } => assert!(params.anns.is_empty()),
                    _ => panic!("inner def"),
                }
            }
            _ => panic!("def"),
        }
    }

    /// Without `annotations` the answer is the reference's: never evaluated
    /// from 3.14 (PEP 649), evaluated before it, refused when unmeasured.
    #[test]
    fn without_annotations_the_reference_minor_decides() {
        use crate::err::{REF_PY_KNOWN, REF_PY_MINOR};
        let src = "from __future__ import division\nif 1:\n    def f(a: X): pass\n";
        if !REF_PY_KNOWN {
            assert_eq!(kind(src), "future");
            return;
        }
        let b = run(src).unwrap();
        let Stmt::If { arms, .. } = &b[0] else { panic!("if") };
        match &arms[0].1[0] {
            Stmt::Def { params, .. } => assert_eq!(params.anns.len(), (REF_PY_MINOR < 14) as usize),
            _ => panic!("def"),
        }
        // An unannotated def is served on every reference.
        assert_eq!(kind("from __future__ import division\ndef f(a): pass\n"), "ok");
    }

    #[test]
    fn everything_else_refuses_as_future() {
        for src in [
            "import os\nfrom __future__ import annotations\n",
            "def f():\n    from __future__ import annotations\n",
            "from __future__ import braces\n",
            "from __future__ import annotations as a\n",
            "from __future__ import annotations\nprint(annotations)\n",
            "from __future__ import annotations\nprint(__annotations__)\n",
            "from __future__ import barry_as_FLUFL\nprint(1 <> 2)\n",
            "\"a\"\n\"b\"\nfrom __future__ import annotations\n",
            "import __future__\n",
            "from __future__ import division\nprint(\u{ff44}ivision)\n",
            "from __future__ import \u{ff42}arry_as_FLUFL\nprint(1 <> 2)\n",
            "from __future__ import division\nprint(f'{\u{ff44}ivision}')\n",
        ] {
            assert_eq!(kind(src), "future", "{src:?}");
        }
    }

    /// What this parser lets through and CPython's compiler rejects, one per
    /// lax note. `tests/test_hold_monotone.py` holds each to CPython 3.14.
    const LAX: &[&str] = &[
        "def f(x: int = 1, y) -> int:\n    return x\n",
        "def f(x: int, x: int): pass\n",
        "f = lambda x, x: 1\n",
        "f = lambda x=1, y: 1\n",
        "def f():\n    break\n",
        "def f():\n    try:\n        pass\n    finally:\n        continue\n",
        "for i in []:\n    def f():\n        break\n",
        "while 0:\n    pass\nelse:\n    break\n",
        "return 1\n",
        "break\n",
        "continue\n",
        "def f(a):\n    global a\n",
        "def f():\n    x = 1\n    global x\n",
        "def f():\n    print(x)\n    global x\n",
        "x = 1\nglobal x\n",
        "def f(*a): pass\nf(x for x in range(2), 1)\n",
        "def f(*a): pass\nf(1, x for x in range(2))\n",
        "def f(*a): pass\nf(x for x in range(2),)\n",
        "print(x=1, 2)\n",
        "print(**{}, *[])\n",
        "print(**{}, 1)\n",
        "print(0777)\n",
        "print(1__0)\n",
        "print(1_)\n",
        "print(1_.5)\n",
        "print(0x1_)\n",
        "try:\n    pass\nexcept:\n    pass\nexcept ValueError:\n    pass\n",
        "print(f'{1!x}')\n",
        "a, *b, *c = [1, 2, 3]\n",
        "for *a, *b in [[1, 2]]: pass\n",
    ];

    /// Every compile-time SyntaxError CPython raises and this parser lets
    /// through is CPython's `SyntaxError` under a head, and `__debug__`
    /// refuses; a legal neighbour of each is still served.
    #[test]
    fn a_syntax_error_cpython_raises_before_running_is_one_under_a_head() {
        const H: &str = "from __future__ import annotations\n";
        for body in LAX {
            assert_eq!(kind(&format!("{H}{body}")), "SyntaxError", "{body:?}");
        }
        for body in ["__debug__ = 1\n", "def f(__debug__): pass\n"] {
            assert_eq!(kind(&format!("{H}{body}")), "future", "{body:?}");
        }
        for body in [
            "def f(x=1, *a, **k): pass\n",
            "def f(a, b=1): pass\n",
            "for i in []:\n    if i:\n        break\n    continue\n",
            "while 0:\n    try:\n        pass\n    finally:\n        continue\n",
            "def f():\n    for i in []:\n        break\n    return 1\n",
            "def f():\n    global x\n    x = 1\n",
            "print(sum(x for x in range(2)))\n",
            "print(*[1], sep='-')\n",
            "print(1, sep='', *[2])\n",
            "print(*[1], **{})\n",
        ] {
            assert_eq!(kind(&format!("{H}{body}")), "ok", "{body:?}");
        }
    }

    /// The same, for a program with no head that the core's walk routes past
    /// itself: each went to CPython before its capability existed. Without the
    /// capability it is the core's program and keeps the core's parse.
    #[test]
    fn a_program_past_the_core_gets_the_syntax_error_too() {
        for head in ["import itertools\n", "import random\nr = random.Random(1)\n", "if 0:\n    import time\n"] {
            for body in LAX {
                assert_eq!(kind(&format!("{head}{body}")), "SyntaxError", "{head:?} {body:?}");
            }
            assert_eq!(kind(&format!("{head}def f(__debug__): pass\n")), "builtin");
        }
        for body in LAX {
            assert_eq!(kind(&format!("import math\n{body}")), "ok", "{body:?}");
        }
        for ok in ["print(0x_1f, 1_000, 0_0, 00, 09.5, 1e1_0, 0o7_7, 1.5e-3)\n", "print(f'{1!r}{2!s}{3!a}')\n"] {
            assert_eq!(kind(&format!("import itertools\n{ok}")), "ok", "{ok:?}");
        }
    }

    /// A NAME `__future__` or `barry_as_FLUFL` that is not the module's import
    /// is an ordinary name: the core runs it, and so does this pass.
    #[test]
    fn only_an_import_of_the_module_is_a_directive() {
        for src in [
            "barry_as_FLUFL = 1\nprint(barry_as_FLUFL)\n",
            "def barry_as_FLUFL(): return 2\n",
            "print(dict(barry_as_FLUFL=1))\n",
            "__future__ = 1\nprint(__future__)\n",
            "for __future__ in range(2): pass\n",
            "import os as __future__\n",
            "def __future__(): return 5\n",
            "print(dict(__future__=1))\n",
        ] {
            assert_eq!(kind(src), "ok", "{src:?}");
        }
        assert_eq!(kind("import __future__\n"), "future");
        assert_eq!(kind("from __future__ import annotations\nbarry_as_FLUFL = 1\n"), "future");
    }
}
