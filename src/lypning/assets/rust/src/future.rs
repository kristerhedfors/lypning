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
//! * `barry_as_FLUFL` ANYWHERE in the source, and before the parse result is
//!   looked at, because it changes the grammar (`1 <> 2` is valid under it)
//!   and the parser would otherwise answer a program CPython runs with a
//!   `SyntaxError` at exit 1;
//! * any other `__future__` in the program: a misplaced import, `import
//!   __future__`, an attribute. Counted, not walked — the pass removes one
//!   `__future__` token per statement it consumed, and any left over refuses;
//! * the imported feature's own name, and `__annotations__`, anywhere else:
//!   the import binds a `_Feature` object and the annotations it defers
//!   become strings, and neither exists here. The corpus uses neither
//!   (mined 2026-09-24), so the refusal costs nothing measured.
//!
//! A program with no `__future__` in it pays one scan of its tokens and is
//! returned exactly as it was parsed.

use crate::ast::*;
use crate::err::{unsupported, R};
use crate::lex::{Tok, Token};
use std::rc::Rc;

/// The feature names served, which is the list `route::MODULE_ATTRS` carries
/// for `__future__` so the CORE's walk sends every other name to CPython.
pub const SERVED: &[&str] = crate::route::FUTURE_SERVED;

/// How many tokens of the program are `needle`: a name, or text inside an
/// f-string, whose expressions the lexer keeps as raw source. Over-counting
/// (an f-string's literal text) can only refuse, never serve.
fn mentions(toks: &[Token], needle: &str) -> usize {
    toks.iter()
        .filter(|t| match &t.tok {
            Tok::Name(n) => n == needle,
            Tok::FStr { raw, .. } => raw.contains(needle),
            _ => false,
        })
        .count()
}

fn refuse(detail: &str) -> crate::err::LypningError {
    unsupported("future", detail)
}

/// The pass. `body` is the parse as it came out, error included, because
/// `barry_as_FLUFL` has to be refused whether or not its grammar parsed.
pub fn pass(body: R<Vec<Stmt>>, toks: &[Token]) -> R<Vec<Stmt>> {
    if mentions(toks, "barry_as_FLUFL") > 0 {
        return Err(refuse("from __future__ import barry_as_FLUFL"));
    }
    let futures = mentions(toks, "__future__");
    if futures == 0 {
        return body;
    }
    let mut body = body?;
    let start = match body.first() {
        Some(Stmt::Expr(Expr::Str(_))) => 1,
        _ => 0,
    };
    let mut end = start;
    let mut names: Vec<Rc<str>> = Vec::new();
    while let Some(Stmt::FromImport { module, names: ns }) = body.get(end) {
        if module.as_ref() != "__future__" {
            break;
        }
        for (n, bind) in ns {
            if n != bind {
                return Err(refuse(&format!("from __future__ import {n} as {bind}")));
            }
            if !SERVED.contains(&n.as_ref()) {
                return Err(refuse(&format!("from __future__ import {n}")));
            }
            names.push(n.clone());
        }
        end += 1;
    }
    if end - start != futures {
        return Err(refuse("__future__ anywhere but the head of the program"));
    }
    // Each consumed name is one token of its own import; any other token
    // spelling it is a use of the `_Feature` binding.
    let own = |n: &str| names.iter().filter(|m| m.as_ref() == n).count();
    for n in names.iter().map(|n| n.as_ref()).chain(["__annotations__"]) {
        if mentions(toks, n) != own(n) {
            return Err(refuse(&format!("the name {n}")));
        }
    }
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
    }
}
