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
//! * the imported feature's own name, and `__annotations__`, anywhere else:
//!   the import binds a `_Feature` object and the annotations it defers
//!   become strings, and neither exists here. The corpus uses neither
//!   (mined 2026-09-24), so the refusal costs nothing measured;
//! * any `__debug__` (CPython rejects every binding of it).
//!
//! Every compile-time `SyntaxError` CPython raises is the parser's, in every
//! variant (`parse::parse`), so nothing here answers one.
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
pub fn pass(body: R<Vec<Stmt>>, toks: &[Token]) -> R<Vec<Stmt>> {
    // Only a program that imports `__future__` is looked at: a variable,
    // a parameter or an argument named `__future__` or `barry_as_FLUFL` is
    // the core's program (`route::future_imported`). The grammar change can
    // only come in through that import, so `future_token_block` below still
    // refuses it whether or not its grammar parsed.
    if !future_imported(toks) {
        return body;
    }
    if let Some(why) = future_token_block(toks) {
        return Err(refuse(why));
    }
    let mut body = body?;
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
        ] {
            assert_eq!(kind(src), "future", "{src:?}");
        }
        // CPython NFKC-folds `ｄivision` into `division`; the lexer refuses
        // every non-ASCII identifier before this pass could miscount one.
        for src in [
            "from __future__ import division\nprint(\u{ff44}ivision)\n",
            "from __future__ import \u{ff42}arry_as_FLUFL\nprint(1 <> 2)\n",
            "from __future__ import division\nprint(f'{\u{ff44}ivision}')\n",
        ] {
            assert_eq!(kind(src), "token", "{src:?}");
        }
    }

    /// What CPython's compiler rejects before anything runs, which the parser
    /// used to let through. `tests/test_hold_monotone.py` holds each to
    /// CPython 3.14.
    const REJECTED: &[&str] = &[
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
        "def g(): pass\nglobal g\n",
        "for x in []: pass\nglobal x\n",
        "try:\n    pass\nexcept ValueError as e:\n    pass\nglobal e\n",
        "print([1 for _ in y])\nglobal y\n",
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
        "print(0x_)\n",
        "try:\n    pass\nexcept:\n    pass\nexcept ValueError:\n    pass\n",
        "print(f'{1!x}')\n",
        "a, *b, *c = [1, 2, 3]\n",
        "for *a, *b in [[1, 2]]: pass\n",
        "*a = [1]\n",
        "for *x in []: pass\n",
        "print([x for *x in []])\n",
        "def f(/, a): pass\n",
        "f = lambda /: 0\n",
        "def f(a, /, /): pass\n",
        "def f(*a, /): pass\n",
        "a, b += 1\n",
    ];

    /// CPython's `TabError`: a `SyntaxError` subclass whose name the engine
    /// cannot spell, so it refuses (`indent`) in every variant and head.
    const TAB_MIXES: &[&str] = &[
        "if 1:\n\tx = 1\n        y = 2\n",
        "if 1:\n        x = 1\n\ty = 2\n",
    ];

    /// Every compile-time SyntaxError is the parser's, with a head, behind a
    /// capability the core lacks, or neither — one answer in every variant.
    #[test]
    fn a_syntax_error_cpython_raises_before_running_is_one_everywhere() {
        for head in ["", "from __future__ import annotations\n", "import itertools\n", "if 0:\n    import time\n"] {
            for body in REJECTED {
                assert_eq!(kind(&format!("{head}{body}")), "SyntaxError", "{head:?} {body:?}");
            }
            for body in TAB_MIXES {
                assert_eq!(kind(&format!("{head}{body}")), "indent", "{head:?} {body:?}");
            }
            for body in ["__debug__ = 1\n", "def f(__debug__): pass\n", "print(__debug__)\n"] {
                assert_eq!(kind(&format!("{head}{body}")), "builtin", "{body:?}");
            }
            for body in ["\u{20ac} = 1\n", "\u{a0}x = 1\n", "x\u{200b} = 1\n", "\u{3c0} = 1\n"] {
                assert_eq!(kind(&format!("{head}{body}")), "token", "{body:?}");
            }
        }
    }

    /// The legal neighbour of each rejection still runs.
    #[test]
    fn the_legal_neighbours_are_served() {
        for body in [
            "def f(x=1, *a, **k): pass\n",
            "def f(a, b=1): pass\n",
            "def f(a, /, b): pass\n",
            "def f(a=1, /, b=2): pass\n",
            "for i in []:\n    if i:\n        break\n    continue\n",
            "while 0:\n    try:\n        pass\n    finally:\n        continue\n",
            "def f():\n    for i in []:\n        break\n    return 1\n",
            "def f():\n    global x\n    x = 1\n",
            "import os\nglobal os\n",
            "from os import path\nglobal path\n",
            "f = lambda x: 0\nglobal x\n",
            "print([x for x in []])\nglobal x\n",
            "print({k: 1 for k in []})\nglobal k\n",
            "def g(x): pass\nglobal x\n",
            "print(dict(y=1))\nglobal y\n",
            "import os\nprint(os.sep)\nglobal sep\n",
            "global x\nglobal x\n",
            "print(sum(x for x in range(2)))\n",
            "print(*[1], sep='-')\n",
            "print(1, sep='', *[2])\n",
            "print(*[1], **{})\n",
            "print(0x_1f, 1_000, 0_0, 00, 09.5, 1e1_0, 0o7_7, 1.5e-3)\n",
            "print(f'{1!r}{2!s}{3!a}')\n",
            "*a, = [1]\n",
            "a, *b = [1, 2]\n",
            "for *x, in [[1]]: pass\n",
            "a = 1\na += 1\n",
            "if 1:\n\tx = 1\n\ty = 2\n",
            "if 1:\n\tif 1:\n\t\tx = 1\n\ty = 2\n",
            "if 1:\n    x = 1\n",
            "print('\u{20ac}')  # \u{20ac}\n",
        ] {
            assert_eq!(kind(body), "ok", "{body:?}");
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
