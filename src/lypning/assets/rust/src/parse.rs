//! Recursive-descent parser for the lypning subset.
//!
//! Every construct outside the subset raises `Unsupported` with the exact kind
//! that blocked it. That is not just an error path: the kinds are the build
//! order (`lypning plan` ranks them by how many corpus entries they unblock), and
//! they are what the router reads to decide which interpreter gets the program.

use crate::ast::*;
use crate::err::{unsupported, LypningError, R};
use crate::lex::{decode_escapes, is_keyword, tokenize, Tok, Token};
use std::rc::Rc;

pub struct Parser {
    t: Vec<Token>,
    i: usize,
    /// How deep the grammar currently is, against [`MAX_PARSE_DEPTH`].
    depth: u32,
    /// Binary operators chained so far, against [`MAX_CHAIN_OPS`]. Counted for
    /// the whole parse rather than per chain: chains compose, and it is the
    /// longest PATH through the tree that the evaluator and the drop both walk.
    chain_ops: u32,
}

/// The nesting a program is allowed, and it is a measurement rather than a
/// taste.
///
/// One level of `(` costs roughly 8 KB of stack here — the precedence chain is
/// a dozen frames deep before it reaches `atom` — and a source file with 1,200
/// of them segfaulted an 8 MB stack. Embedded that segfault belongs to the
/// HOST, and a stack overflow is not an unwind, so no guard at the ABI boundary
/// can catch it: it has to be refused before it is reached.
///
/// 64 is where the two ends meet. The deepest program in the harvested corpus
/// (842 entries, loaded 2026-08-20) nests 18, the 99th percentile nests 11, and
/// the median nests 2 — so 64 is three and a half times the deepest thing an
/// agent has ever actually typed, while costing at most half a megabyte of
/// stack, which is safe on a host thread rather than only on a main one.
/// CPython refuses these too (`SyntaxError: too many nested parentheses`), so
/// routing one onward gets the caller an error either way — the difference is
/// only whether it arrives as an error or as a signal.
pub const MAX_PARSE_DEPTH: u32 = 64;

/// How many binary operators one program may chain together, in total.
///
/// A separate limit from the one above because a chain is not nesting:
/// `1+1+1+…` is parsed by an iterative loop (`bin_level`) into a LEFT-LEANING
/// tree, so a flat chain of a hundred thousand terms parses without recursing
/// at all — and then two things walk that spine one frame per node. The
/// evaluator does (`eval::MAX_EXPR_DEPTH` catches that), and so does the AST's
/// own derived `Drop`, which nothing can catch: the tree is dropped after the
/// refusal, so bounding only the evaluator still segfaulted the host on a 1 MB
/// thread.
///
/// So the tree is never built that deep. A one-liner with a thousand binary
/// operators in it is not a program anyone typed, and a program that has one
/// gets its answer from CPython.
pub const MAX_CHAIN_OPS: u32 = 1000;

pub fn parse(src: &str) -> R<Vec<Stmt>> {
    let mut p = Parser {
        t: tokenize(src)?,
        i: 0,
        depth: 0,
        chain_ops: 0,
    };
    let mut body = Vec::new();
    while !p.at_eof() {
        if p.eat_newline() {
            continue;
        }
        body.extend(p.statement()?);
    }
    Ok(body)
}

impl Parser {
    fn peek(&self) -> &Tok {
        &self.t[self.i.min(self.t.len() - 1)].tok
    }
    fn peek_at(&self, n: usize) -> &Tok {
        &self.t[(self.i + n).min(self.t.len() - 1)].tok
    }
    fn line(&self) -> u32 {
        self.t[self.i.min(self.t.len() - 1)].line
    }
    fn at_eof(&self) -> bool {
        matches!(self.peek(), Tok::Eof)
    }
    fn bump(&mut self) -> Tok {
        let t = self.t[self.i.min(self.t.len() - 1)].tok.clone();
        if self.i < self.t.len() - 1 {
            self.i += 1;
        }
        t
    }
    fn is_op(&self, s: &str) -> bool {
        matches!(self.peek(), Tok::Op(o) if *o == s)
    }
    fn eat_op(&mut self, s: &str) -> bool {
        if self.is_op(s) {
            self.bump();
            true
        } else {
            false
        }
    }
    fn expect_op(&mut self, s: &str) -> R<()> {
        if self.eat_op(s) {
            Ok(())
        } else {
            Err(LypningError::syntax(
                self.line(),
                &format!("expected '{s}', found {}", self.describe()),
            ))
        }
    }
    fn is_kw(&self, s: &str) -> bool {
        matches!(self.peek(), Tok::Name(n) if n == s)
    }
    fn eat_kw(&mut self, s: &str) -> bool {
        if self.is_kw(s) {
            self.bump();
            true
        } else {
            false
        }
    }
    fn expect_kw(&mut self, s: &str) -> R<()> {
        if self.eat_kw(s) {
            Ok(())
        } else {
            Err(LypningError::syntax(
                self.line(),
                &format!("expected '{s}', found {}", self.describe()),
            ))
        }
    }
    fn describe(&self) -> String {
        match self.peek() {
            Tok::Name(n) => format!("'{n}'"),
            Tok::Op(o) => format!("'{o}'"),
            Tok::Newline => "end of line".into(),
            Tok::Indent => "an indent".into(),
            Tok::Dedent => "a dedent".into(),
            Tok::Eof => "end of input".into(),
            _ => "a literal".into(),
        }
    }
    fn eat_newline(&mut self) -> bool {
        if matches!(self.peek(), Tok::Newline) {
            self.bump();
            true
        } else {
            false
        }
    }
    fn ident(&mut self) -> R<Rc<str>> {
        match self.peek().clone() {
            Tok::Name(n) if !is_keyword(&n) => {
                self.bump();
                Ok(n.into())
            }
            _ => Err(LypningError::syntax(
                self.line(),
                &format!("expected a name, found {}", self.describe()),
            )),
        }
    }

    // ---- statements -------------------------------------------------------

    /// One logical line, which may hold several `;`-separated simple statements.
    fn statement(&mut self) -> R<Vec<Stmt>> {
        if self.is_compound() {
            return Ok(vec![self.compound()?]);
        }
        let mut out = vec![self.simple()?];
        while self.eat_op(";") {
            if matches!(self.peek(), Tok::Newline | Tok::Eof) {
                break;
            }
            out.push(self.simple()?);
        }
        if !self.eat_newline() && !self.at_eof() {
            return Err(LypningError::syntax(
                self.line(),
                &format!("invalid syntax: unexpected {}", self.describe()),
            ));
        }
        Ok(out)
    }

    fn is_compound(&self) -> bool {
        match self.peek() {
            Tok::Name(n) => matches!(
                n.as_str(),
                "if" | "for" | "while" | "def" | "try" | "with" | "class" | "async"
            ),
            Tok::Op("@") => true,
            _ => false,
        }
    }

    fn block(&mut self) -> R<Vec<Stmt>> {
        self.nested("block", |p| p.block_inner())
    }

    fn block_inner(&mut self) -> R<Vec<Stmt>> {
        self.expect_op(":")?;
        if self.eat_newline() {
            if !matches!(self.peek(), Tok::Indent) {
                return Err(LypningError::syntax(self.line(), "expected an indented block"));
            }
            self.bump();
            let mut body = Vec::new();
            loop {
                if matches!(self.peek(), Tok::Dedent) {
                    self.bump();
                    break;
                }
                if self.at_eof() {
                    break;
                }
                if self.eat_newline() {
                    continue;
                }
                body.extend(self.statement()?);
            }
            Ok(body)
        } else {
            // Suite on the same line: `if x: a; b`
            let mut out = vec![self.simple()?];
            while self.eat_op(";") {
                if matches!(self.peek(), Tok::Newline | Tok::Eof) {
                    break;
                }
                out.push(self.simple()?);
            }
            self.eat_newline();
            Ok(out)
        }
    }

    fn compound(&mut self) -> R<Stmt> {
        if self.is_op("@") {
            return Err(unsupported("decorator", "decorated definition"));
        }
        if self.is_kw("class") {
            return Err(unsupported("class", "class definition"));
        }
        if self.is_kw("async") {
            return Err(unsupported("async", "async def / async for"));
        }
        if self.eat_kw("if") {
            let mut arms = Vec::new();
            let cond = self.expr()?;
            arms.push((cond, self.block()?));
            let mut els = Vec::new();
            loop {
                if self.eat_kw("elif") {
                    let c = self.expr()?;
                    arms.push((c, self.block()?));
                } else if self.eat_kw("else") {
                    els = self.block()?;
                    break;
                } else {
                    break;
                }
            }
            return Ok(Stmt::If { arms, els });
        }
        if self.eat_kw("while") {
            let cond = self.expr()?;
            let body = self.block()?;
            let els = if self.eat_kw("else") {
                self.block()?
            } else {
                Vec::new()
            };
            return Ok(Stmt::While { cond, body, els });
        }
        if self.eat_kw("for") {
            let target = self.target_list("in")?;
            self.expect_kw("in")?;
            let iter = self.expr_list()?;
            let body = self.block()?;
            let els = if self.eat_kw("else") {
                self.block()?
            } else {
                Vec::new()
            };
            return Ok(Stmt::For {
                target,
                iter,
                body,
                els,
            });
        }
        if self.eat_kw("def") {
            let name = self.ident()?;
            let params = self.params()?;
            if self.eat_op("->") {
                self.expr()?; // return annotation: parsed and discarded, as CPython does at runtime
            }
            let body = self.block()?;
            if contains_yield(&body) {
                return Err(unsupported("generator", "yield in a function body"));
            }
            return Ok(Stmt::Def {
                name,
                params: Rc::new(params),
                body: Rc::new(body),
            });
        }
        if self.eat_kw("with") {
            let mut items = Vec::new();
            let parenthesized = self.is_op("(") && { let r = self.with_items_parenthesized(); r };
            if parenthesized {
                self.bump();
            }
            loop {
                let ctx = self.expr()?;
                let alias = if self.eat_kw("as") {
                    Some(self.target_atom()?)
                } else {
                    None
                };
                items.push((ctx, alias));
                if !self.eat_op(",") {
                    break;
                }
                if parenthesized && self.is_op(")") {
                    break;
                }
            }
            if parenthesized {
                self.expect_op(")")?;
            }
            let body = self.block()?;
            return Ok(Stmt::With { items, body });
        }
        if self.eat_kw("try") {
            let body = self.block()?;
            let mut handlers = Vec::new();
            while self.is_kw("except") {
                self.bump();
                if self.is_op("*") {
                    return Err(unsupported("except-star", "except* group"));
                }
                let mut kinds = Vec::new();
                let mut name = None;
                if !self.is_op(":") {
                    if self.eat_op("(") {
                        loop {
                            kinds.push(self.dotted_name()?);
                            if !self.eat_op(",") {
                                break;
                            }
                            if self.is_op(")") {
                                break;
                            }
                        }
                        self.expect_op(")")?;
                    } else {
                        kinds.push(self.dotted_name()?);
                    }
                    if self.eat_kw("as") {
                        name = Some(self.ident()?);
                    }
                }
                let hbody = self.block()?;
                handlers.push(Handler {
                    kinds,
                    name,
                    body: hbody,
                });
            }
            let els = if self.eat_kw("else") {
                self.block()?
            } else {
                Vec::new()
            };
            let finally = if self.eat_kw("finally") {
                self.block()?
            } else {
                Vec::new()
            };
            return Ok(Stmt::Try {
                body,
                handlers,
                els,
                finally,
            });
        }
        Err(LypningError::syntax(self.line(), "invalid syntax"))
    }

    /// Distinguish `with (a, b):` (a parenthesized item list) from
    /// `with (expr):` — scan for a top-level `as` or a `,` before the `:`.
    fn with_items_parenthesized(&self) -> bool {
        let mut depth = 0i32;
        let mut j = self.i;
        while j < self.t.len() {
            match &self.t[j].tok {
                Tok::Op("(") | Tok::Op("[") | Tok::Op("{") => depth += 1,
                Tok::Op(")") | Tok::Op("]") | Tok::Op("}") => {
                    depth -= 1;
                    if depth == 0 {
                        // The paren group closes; it is an item list only if a
                        // ':' follows immediately.
                        return matches!(self.t.get(j + 1).map(|t| &t.tok), Some(Tok::Op(":")))
                            && self.t[self.i + 1..j]
                                .iter()
                                .any(|t| matches!(&t.tok, Tok::Name(n) if n=="as"));
                    }
                }
                Tok::Eof => break,
                _ => {}
            }
            j += 1;
        }
        false
    }

    fn dotted_name(&mut self) -> R<Rc<str>> {
        let mut s = self.ident()?.to_string();
        while self.is_op(".") {
            self.bump();
            s.push('.');
            s.push_str(&self.ident()?);
        }
        Ok(s.into())
    }

    fn params(&mut self) -> R<Params> {
        self.expect_op("(")?;
        let mut p = Params::default();
        loop {
            if self.is_op(")") {
                break;
            }
            if self.eat_op("/") {
                // positional-only marker: accepted and ignored
                if !self.eat_op(",") {
                    break;
                }
                continue;
            }
            if self.eat_op("*") {
                if self.is_op(",") || self.is_op(")") {
                    return Err(unsupported("kwonly", "keyword-only parameters"));
                }
                p.star = Some(p.names.len());
                p.names.push(self.ident()?);
                p.defaults.push(None);
            } else if self.eat_op("**") {
                p.dstar = Some(p.names.len());
                p.names.push(self.ident()?);
                p.defaults.push(None);
            } else {
                // A NAME AFTER `*args` IS KEYWORD-ONLY, exactly as one after a
                // bare `*` is, and the bare form is refused four lines up. This
                // one used to fall through and be recorded as an ordinary
                // positional parameter — which the binder cannot represent,
                // because it computes the positional count as
                // `names.len() - star - dstar` and then slices `names[..npos]`
                // FROM THE FRONT. That is only the positional parameters while
                // `*args` and `**kw` come last.
                //
                //     def f(a, *c, d): return (a, c, d)
                //     f(1, 2, d=3)   CPython (1, (2,), 3)   this: unexpected keyword 'd'
                //     f(1, 2, 3)     CPython TypeError       this: UnboundLocalError
                //
                // Neither is a refusal, so neither could be answered one spawn
                // later. Refusing here makes the two spellings of the same
                // feature behave the same way.
                if p.star.is_some() {
                    return Err(unsupported("kwonly", "keyword-only parameters"));
                }
                let n = self.ident()?;
                if self.eat_op(":") {
                    self.expr()?; // annotation, discarded
                }
                let d = if self.eat_op("=") {
                    Some(self.expr()?)
                } else {
                    None
                };
                p.names.push(n);
                p.defaults.push(d);
            }
            if !self.eat_op(",") {
                break;
            }
        }
        self.expect_op(")")?;
        Ok(p)
    }

    fn simple(&mut self) -> R<Stmt> {
        if self.eat_kw("pass") {
            return Ok(Stmt::Pass);
        }
        if self.eat_kw("break") {
            return Ok(Stmt::Break);
        }
        if self.eat_kw("continue") {
            return Ok(Stmt::Continue);
        }
        if self.is_kw("nonlocal") {
            return Err(unsupported("nonlocal", "nonlocal declaration"));
        }
        if self.eat_kw("global") {
            let mut names = vec![self.ident()?];
            while self.eat_op(",") {
                names.push(self.ident()?);
            }
            return Ok(Stmt::Global(names));
        }
        if self.eat_kw("return") {
            if matches!(self.peek(), Tok::Newline | Tok::Eof) || self.is_op(";") {
                return Ok(Stmt::Return(None));
            }
            return Ok(Stmt::Return(Some(self.expr_list()?)));
        }
        if self.eat_kw("raise") {
            if matches!(self.peek(), Tok::Newline | Tok::Eof) || self.is_op(";") {
                return Ok(Stmt::Raise { exc: None });
            }
            let e = self.expr()?;
            if self.eat_kw("from") {
                self.expr()?;
            }
            return Ok(Stmt::Raise { exc: Some(e) });
        }
        if self.eat_kw("assert") {
            let test = self.expr()?;
            let msg = if self.eat_op(",") {
                Some(self.expr()?)
            } else {
                None
            };
            return Ok(Stmt::Assert { test, msg });
        }
        if self.eat_kw("del") {
            let first = self.clone_expr()?;
            let mut targets = vec![self.target_from_expr(first)?];
            while self.eat_op(",") {
                if matches!(self.peek(), Tok::Newline | Tok::Eof) {
                    break;
                }
                let e = self.clone_expr()?;
                targets.push(self.target_from_expr(e)?);
            }
            return Ok(Stmt::Del(targets));
        }
        if self.eat_kw("import") {
            let mut names = Vec::new();
            loop {
                let m = self.dotted_name()?;
                let bind = if self.eat_kw("as") {
                    self.ident()?
                } else {
                    // `import a.b` binds `a`.
                    m.split('.').next().unwrap().into()
                };
                names.push((m, bind));
                if !self.eat_op(",") {
                    break;
                }
            }
            return Ok(Stmt::Import { names });
        }
        if self.eat_kw("from") {
            if self.is_op(".") {
                return Err(unsupported("import", "relative import"));
            }
            let module = self.dotted_name()?;
            self.expect_kw("import")?;
            if self.eat_op("*") {
                return Err(unsupported("import", "star import"));
            }
            let paren = self.eat_op("(");
            let mut names = Vec::new();
            loop {
                let n = self.ident()?;
                let bind = if self.eat_kw("as") { self.ident()? } else { n.clone() };
                names.push((n, bind));
                if !self.eat_op(",") {
                    break;
                }
                if paren && self.is_op(")") {
                    break;
                }
            }
            if paren {
                self.expect_op(")")?;
            }
            return Ok(Stmt::FromImport { module, names });
        }

        // Expression, assignment or augmented assignment.
        let first = self.expr_list()?;
        const AUG: &[(&str, BinOp)] = &[
            ("+=", BinOp::Add),
            ("-=", BinOp::Sub),
            ("*=", BinOp::Mul),
            ("/=", BinOp::Div),
            ("//=", BinOp::FloorDiv),
            ("%=", BinOp::Mod),
            ("**=", BinOp::Pow),
            ("&=", BinOp::BitAnd),
            ("|=", BinOp::BitOr),
            ("^=", BinOp::BitXor),
            ("<<=", BinOp::LShift),
            (">>=", BinOp::RShift),
        ];
        for (op, b) in AUG {
            if self.is_op(op) {
                self.bump();
                let value = self.expr_list()?;
                return Ok(Stmt::AugAssign {
                    target: self.target_from_expr(first)?,
                    op: *b,
                    value,
                });
            }
        }
        if self.is_op(":") {
            // Annotated assignment: `x: int = 1`
            self.bump();
            self.expr()?;
            if self.eat_op("=") {
                let value = self.expr_list()?;
                return Ok(Stmt::Assign {
                    targets: vec![self.target_from_expr(first)?],
                    value,
                });
            }
            return Ok(Stmt::Pass);
        }
        if self.is_op("=") {
            let mut targets = vec![self.target_from_expr(first)?];
            let mut value = None;
            while self.eat_op("=") {
                let e = self.expr_list()?;
                if self.is_op("=") {
                    targets.push(self.target_from_expr(e)?);
                } else {
                    value = Some(e);
                }
            }
            return Ok(Stmt::Assign {
                targets,
                value: value.unwrap(),
            });
        }
        Ok(Stmt::Expr(first))
    }

    fn clone_expr(&mut self) -> R<Expr> {
        self.expr()
    }

    // ---- targets ----------------------------------------------------------

    fn target_atom(&mut self) -> R<Target> {
        let e = self.unary()?;
        let e = self.trailers(e)?;
        self.target_from_expr(e)
    }

    /// Parse a target list up to `stop` (a keyword), e.g. the `for` target.
    fn target_list(&mut self, stop: &str) -> R<Target> {
        let mut items = Vec::new();
        // A TRAILING COMMA AFTER ONE NAME MAKES A ONE-ELEMENT TUPLE TARGET, and
        // dropping it dropped the unpacking with it. `for v, in [(1,)]` bound
        // the whole tuple — `(1,)` rather than `1` — and, worse, the ARITY
        // CHECK vanished entirely:
        //
        //     [v for v, in [(1, 2)]]
        //     CPython  ValueError: too many values to unpack (expected 1)
        //     this     [(1, 2)]        exit 0
        //
        // A program CPython stops with an exception ran to completion and
        // printed plausible wrong data. The parenthesized spelling `(v,)` was
        // always right and two names `a, b,` were always right, which is what
        // kept this quiet: only the unparenthesized single name loses its comma.
        // It reached statement for-loops and all four comprehension forms.
        let mut saw_comma = false;
        loop {
            if self.eat_op("*") {
                items.push(Target::Star(Box::new(self.target_atom()?)));
            } else {
                items.push(self.target_atom()?);
            }
            if !self.eat_op(",") {
                break;
            }
            saw_comma = true;
            if self.is_kw(stop) {
                break;
            }
        }
        Ok(if items.len() == 1 && !saw_comma {
            items.pop().unwrap()
        } else {
            Target::Tuple(items)
        })
    }

    fn target_from_expr(&self, e: Expr) -> R<Target> {
        Ok(match e {
            Expr::Name(name) => Target::Name(name),
            Expr::Starred(inner) => Target::Star(Box::new(self.target_from_expr(*inner)?)),
            Expr::Tuple(v) | Expr::List(v) => {
                let mut out = Vec::with_capacity(v.len());
                for x in v {
                    out.push(self.target_from_expr(x)?);
                }
                Target::Tuple(out)
            }
            Expr::Un(UnOp::Pos, _) => return Err(LypningError::syntax(self.line(), "invalid target")),
            Expr::Attr(b, n) => Target::Attr(*b, n),
            Expr::Index(b, i) => Target::Index(*b, *i),
            Expr::Slice {
                base,
                lo,
                hi,
                step: None,
            } => Target::Slice {
                base: *base,
                lo: lo.map(|x| *x),
                hi: hi.map(|x| *x),
            },
            Expr::Slice { .. } => return Err(unsupported("slice-assign", "extended slice assignment")),
            _ => return Err(LypningError::syntax(self.line(), "cannot assign to expression")),
        })
    }

    // ---- expressions ------------------------------------------------------

    /// A bare expression list — `a, b` builds a tuple. Used where Python's
    /// grammar allows `testlist`.
    fn expr_list(&mut self) -> R<Expr> {
        // A leading `*` at element position is a starred TARGET (`a, *rest = …`)
        // or a starred display element — never multiplication, which needs a
        // left operand.
        if self.is_op("*") {
            self.bump();
            let e = self.expr()?;
            let mut items = vec![Expr::Starred(Box::new(e))];
            while self.eat_op(",") {
                if matches!(self.peek(), Tok::Newline | Tok::Eof) || self.is_op("=") {
                    break;
                }
                items.push(self.star_element()?);
            }
            return Ok(Expr::Tuple(items));
        }
        let first = self.expr()?;
        if !self.is_op(",") {
            return Ok(first);
        }
        let mut items = vec![first];
        while self.eat_op(",") {
            if matches!(self.peek(), Tok::Newline | Tok::Eof)
                || self.is_op("=")
                || self.is_op(")")
                || self.is_op("]")
                || self.is_op("}")
                || self.is_op(";")
                || self.is_op(":")
            {
                break;
            }
            items.push(self.star_element()?);
        }
        Ok(Expr::Tuple(items))
    }

    fn star_element(&mut self) -> R<Expr> {
        if self.is_op("*") {
            self.bump();
            return Ok(Expr::Starred(Box::new(self.expr()?)));
        }
        self.expr()
    }

    pub fn expr(&mut self) -> R<Expr> {
        // A CONSTRUCT THIS PARSER DOES NOT KNOW IS A CAPABILITY GAP, NOT A
        // SYNTAX ERROR, and the difference is the exit code. docs/HILLCLIMB.md
        // iteration 14 draws the line: a SyntaxError is terminal, so `$p` — which
        // cannot begin a token in ANY Python program — exits 1 rather than
        // spending a spawn to be told by CPython what lypning already knew.
        //
        // The converse had no such care. `print((n := 1))` is a valid program,
        // and it exited 1 with `SyntaxError: expected ')', found ':='` — the
        // PROGRAM's own exit, which the chain does not retry, so a program
        // CPython runs fine simply died. The classifier contains it today
        // (`route` reports `syntax` and sends it to CPython), but the binary is
        // an interpreter someone runs directly and the conformance arm scores
        // it as a MISMATCH.
        //
        // So: syntax this parser can RECOGNISE AND NAME refuses, like `async`,
        // `kwonly` and `nonlocal` already do. Genuinely invalid syntax keeps
        // exiting 1, which is the decision iteration 14 made on purpose.
        if self.is_kw("lambda") {
            self.bump();
            let mut p = Params::default();
            while !self.is_op(":") {
                if self.eat_op("*") {
                    p.star = Some(p.names.len());
                    p.names.push(self.ident()?);
                    p.defaults.push(None);
                } else {
                    let n = self.ident()?;
                    let d = if self.eat_op("=") {
                        Some(self.ternary_tail_free()?)
                    } else {
                        None
                    };
                    p.names.push(n);
                    p.defaults.push(d);
                }
                if !self.eat_op(",") {
                    break;
                }
            }
            self.expect_op(":")?;
            let body = self.expr()?;
            return Ok(Expr::Lambda {
                params: Rc::new(p),
                body: Box::new(body),
            });
        }
        if self.is_kw("yield") {
            return Err(unsupported("generator", "yield expression"));
        }
        if self.is_kw("await") {
            return Err(unsupported("async", "await expression"));
        }
        let e = self.or_test()?;
        if self.is_kw("if") {
            self.bump();
            let cond = self.or_test()?;
            self.expect_kw("else")?;
            let els = self.expr()?;
            return Ok(Expr::Cond {
                cond: Box::new(cond),
                then: Box::new(e),
                els: Box::new(els),
            });
        }
        Ok(e)
    }

    /// A default value inside a lambda parameter list — no top-level ternary,
    /// because the `:` would be ambiguous with the lambda body.
    fn ternary_tail_free(&mut self) -> R<Expr> {
        self.or_test()
    }

    fn or_test(&mut self) -> R<Expr> {
        let mut items = vec![self.and_test()?];
        // `x := 1` — see the note on `expr`. The check sits after the left-hand
        // side because the walrus FOLLOWS its target, so the token is not seen
        // until the name has been parsed. Anywhere earlier and the parser has
        // already reported "expected ')'" instead of naming the construct.
        if self.is_op(":=") {
            return Err(unsupported("walrus", "assignment expression (:=)"));
        }
        while self.is_kw("or") {
            self.bump();
            items.push(self.and_test()?);
        }
        Ok(if items.len() == 1 {
            items.pop().unwrap()
        } else {
            Expr::BoolOr(items)
        })
    }
    fn and_test(&mut self) -> R<Expr> {
        let mut items = vec![self.not_test()?];
        while self.is_kw("and") {
            self.bump();
            items.push(self.not_test()?);
        }
        Ok(if items.len() == 1 {
            items.pop().unwrap()
        } else {
            Expr::BoolAnd(items)
        })
    }
    fn not_test(&mut self) -> R<Expr> {
        if self.is_kw("not") {
            self.bump();
            return Ok(Expr::Un(UnOp::Not, Box::new(self.not_test()?)));
        }
        self.comparison()
    }

    fn comparison(&mut self) -> R<Expr> {
        let first = self.bitor()?;
        let mut rest = Vec::new();
        loop {
            let op = if self.is_op("<") {
                CmpOp::Lt
            } else if self.is_op("<=") {
                CmpOp::Le
            } else if self.is_op(">") {
                CmpOp::Gt
            } else if self.is_op(">=") {
                CmpOp::Ge
            } else if self.is_op("==") {
                CmpOp::Eq
            } else if self.is_op("!=") {
                CmpOp::Ne
            } else if self.is_kw("in") {
                CmpOp::In
            } else if self.is_kw("not") && matches!(self.peek_at(1), Tok::Name(n) if n == "in") {
                self.bump();
                CmpOp::NotIn
            } else if self.is_kw("is") {
                if matches!(self.peek_at(1), Tok::Name(n) if n == "not") {
                    self.bump();
                    self.bump();
                    rest.push((CmpOp::IsNot, self.bitor()?));
                    continue;
                }
                CmpOp::Is
            } else {
                break;
            };
            self.bump();
            rest.push((op, self.bitor()?));
        }
        Ok(if rest.is_empty() {
            first
        } else {
            Expr::Compare {
                first: Box::new(first),
                rest,
            }
        })
    }

    fn bin_level(
        &mut self,
        ops: &[(&str, BinOp)],
        next: fn(&mut Self) -> R<Expr>,
    ) -> R<Expr> {
        let mut lhs = next(self)?;
        'outer: loop {
            for (s, op) in ops {
                if self.is_op(s) {
                    self.chain_ops += 1;
                    if self.chain_ops > MAX_CHAIN_OPS {
                        return Err(unsupported(
                            "recursion",
                            &format!("more than {MAX_CHAIN_OPS} chained operators"),
                        ));
                    }
                    self.bump();
                    let rhs = next(self)?;
                    lhs = Expr::Bin(*op, Box::new(lhs), Box::new(rhs));
                    continue 'outer;
                }
            }
            break;
        }
        Ok(lhs)
    }

    fn bitor(&mut self) -> R<Expr> {
        self.bin_level(&[("|", BinOp::BitOr)], Self::bitxor)
    }
    fn bitxor(&mut self) -> R<Expr> {
        self.bin_level(&[("^", BinOp::BitXor)], Self::bitand)
    }
    fn bitand(&mut self) -> R<Expr> {
        self.bin_level(&[("&", BinOp::BitAnd)], Self::shift)
    }
    fn shift(&mut self) -> R<Expr> {
        self.bin_level(&[("<<", BinOp::LShift), (">>", BinOp::RShift)], Self::arith)
    }
    fn arith(&mut self) -> R<Expr> {
        self.bin_level(&[("+", BinOp::Add), ("-", BinOp::Sub)], Self::term)
    }
    fn term(&mut self) -> R<Expr> {
        self.bin_level(
            &[
                ("*", BinOp::Mul),
                ("/", BinOp::Div),
                ("//", BinOp::FloorDiv),
                ("%", BinOp::Mod),
            ],
            Self::unary,
        )
    }

    fn unary(&mut self) -> R<Expr> {
        if self.is_op("-") {
            self.bump();
            let e = self.unary()?;
            // Fold `-<literal>` so integer-literal range checks read naturally.
            return Ok(match e {
                Expr::Int(v) => Expr::Int(-v),
                Expr::Float(v) => Expr::Float(-v),
                other => Expr::Un(UnOp::Neg, Box::new(other)),
            });
        }
        if self.is_op("+") {
            self.bump();
            return Ok(Expr::Un(UnOp::Pos, Box::new(self.unary()?)));
        }
        if self.is_op("~") {
            self.bump();
            return Ok(Expr::Un(UnOp::Invert, Box::new(self.unary()?)));
        }
        self.power()
    }

    fn power(&mut self) -> R<Expr> {
        let base = self.atom_trailers()?;
        if self.is_op("**") {
            self.bump();
            // right-associative, and binds tighter than a leading unary minus
            let exp = self.unary()?;
            return Ok(Expr::Bin(BinOp::Pow, Box::new(base), Box::new(exp)));
        }
        Ok(base)
    }

    fn atom_trailers(&mut self) -> R<Expr> {
        let a = self.atom()?;
        self.trailers(a)
    }

    fn trailers(&mut self, mut e: Expr) -> R<Expr> {
        loop {
            if self.is_op(".") {
                self.bump();
                let n = self.ident()?;
                e = Expr::Attr(Box::new(e), n);
            } else if self.is_op("(") {
                self.bump();
                e = self.call_tail(e)?;
            } else if self.is_op("[") {
                self.bump();
                e = self.subscript_tail(e)?;
            } else {
                return Ok(e);
            }
        }
    }

    fn call_tail(&mut self, func: Expr) -> R<Expr> {
        let mut args = Vec::new();
        let mut star = Vec::new();
        let mut kwargs = Vec::new();
        let mut dstar = Vec::new();
        loop {
            if self.is_op(")") {
                break;
            }
            if self.eat_op("**") {
                dstar.push(self.expr()?);
            } else if self.eat_op("*") {
                star.push(args.len());
                args.push(self.expr()?);
            } else if matches!(self.peek(), Tok::Name(n) if !is_keyword(n))
                && matches!(self.peek_at(1), Tok::Op("="))
            {
                let n = self.ident()?;
                self.bump();
                kwargs.push((n, self.expr()?));
            } else {
                let e = self.expr()?;
                // A bare generator argument: `sum(x for x in y)`
                if self.is_kw("for") {
                    let clauses = self.comp_clauses()?;
                    args.push(Expr::Comp {
                        kind: CompKind::Gen,
                        elt: Box::new(e),
                        val: None,
                        clauses,
                    });
                } else {
                    args.push(e);
                }
            }
            if !self.eat_op(",") {
                break;
            }
        }
        self.expect_op(")")?;
        Ok(Expr::Call {
            func: Box::new(func),
            args,
            star,
            kwargs,
            dstar,
        })
    }

    fn subscript_tail(&mut self, base: Expr) -> R<Expr> {
        // `a[:]`, `a[i]`, `a[i:j]`, `a[i:j:k]`
        let lo = if self.is_op(":") {
            None
        } else {
            Some(Box::new(self.expr()?))
        };
        if self.eat_op(":") {
            let hi = if self.is_op("]") || self.is_op(":") {
                None
            } else {
                Some(Box::new(self.expr()?))
            };
            let step = if self.eat_op(":") {
                if self.is_op("]") {
                    None
                } else {
                    Some(Box::new(self.expr()?))
                }
            } else {
                None
            };
            // `x[0:1, 2]` is a TUPLE holding a slice, which CPython builds and
            // hands to the container — a list then raises "list indices must be
            // integers or slices, not tuple". This parser has no slice VALUE to
            // put in a tuple, so it used to run off the end of the slice and
            // report `expected ']', found ','` at exit 1: a valid program, dead.
            if self.is_op(",") {
                return Err(unsupported(
                    "subscript",
                    "a tuple subscript containing a slice, e.g. x[0:1, 2]",
                ));
            }
            self.expect_op("]")?;
            return Ok(Expr::Slice {
                base: Box::new(base),
                lo,
                hi,
                step,
            });
        }
        // `a[i, j]` — a tuple key, which dicts legitimately use.
        let mut idx = *lo.unwrap();
        if self.is_op(",") {
            let mut items = vec![idx];
            while self.eat_op(",") {
                if self.is_op("]") {
                    break;
                }
                items.push(self.expr()?);
            }
            idx = Expr::Tuple(items);
        }
        self.expect_op("]")?;
        Ok(Expr::Index(Box::new(base), Box::new(idx)))
    }

    fn comp_clauses(&mut self) -> R<Vec<CompClause>> {
        let mut clauses = Vec::new();
        while self.eat_kw("for") {
            let target = self.target_list("in")?;
            self.expect_kw("in")?;
            let iter = self.or_test()?;
            let mut ifs = Vec::new();
            while self.is_kw("if") {
                self.bump();
                ifs.push(self.or_test()?);
            }
            clauses.push(CompClause { target, iter, ifs });
        }
        Ok(clauses)
    }

    /// One level deeper, and back out again however this returns.
    ///
    /// Written as a wrapper rather than an increment inside each body because
    /// both callees are threaded with `?`: a hand-balanced counter would leak a
    /// level on every syntax error, and a long-lived host would watch its own
    /// nesting limit tighten with each bad program it was handed.
    fn nested<T>(&mut self, what: &str, f: impl FnOnce(&mut Self) -> R<T>) -> R<T> {
        self.depth += 1;
        if self.depth > MAX_PARSE_DEPTH {
            self.depth -= 1;
            return Err(unsupported(
                "recursion",
                &format!("{what} nested deeper than {MAX_PARSE_DEPTH}"),
            ));
        }
        let r = f(self);
        self.depth -= 1;
        r
    }

    fn atom(&mut self) -> R<Expr> {
        self.nested("expression", |p| p.atom_inner())
    }

    fn atom_inner(&mut self) -> R<Expr> {
        match self.peek().clone() {
            Tok::Int(v) => {
                self.bump();
                Ok(Expr::Int(v))
            }
            Tok::Float(v) => {
                self.bump();
                Ok(Expr::Float(v))
            }
            Tok::Str { .. } | Tok::FStr { .. } => self.string_group(),
            Tok::Name(n) => {
                match n.as_str() {
                    "None" => {
                        self.bump();
                        return Ok(Expr::None);
                    }
                    "True" => {
                        self.bump();
                        return Ok(Expr::True);
                    }
                    "False" => {
                        self.bump();
                        return Ok(Expr::False);
                    }
                    _ => {}
                }
                if is_keyword(&n) {
                    return Err(LypningError::syntax(
                        self.line(),
                        &format!("invalid syntax near '{n}'"),
                    ));
                }
                self.bump();
                Ok(Expr::Name(n.into()))
            }
            Tok::Op("(") => {
                self.bump();
                if self.eat_op(")") {
                    return Ok(Expr::Tuple(Vec::new()));
                }
                let first = if self.eat_op("*") {
                    return Err(unsupported("unpack", "* in a parenthesized display"));
                } else {
                    self.expr()?
                };
                if self.is_kw("for") {
                    let clauses = self.comp_clauses()?;
                    self.expect_op(")")?;
                    return Ok(Expr::Comp {
                        kind: CompKind::Gen,
                        elt: Box::new(first),
                        val: None,
                        clauses,
                    });
                }
                if self.eat_op(",") {
                    let mut items = vec![first];
                    while !self.is_op(")") {
                        items.push(self.expr()?);
                        if !self.eat_op(",") {
                            break;
                        }
                    }
                    self.expect_op(")")?;
                    return Ok(Expr::Tuple(items));
                }
                self.expect_op(")")?;
                Ok(first)
            }
            Tok::Op("[") => {
                self.bump();
                if self.eat_op("]") {
                    return Ok(Expr::List(Vec::new()));
                }
                // The parenthesized twin of this is refused twenty lines down;
                // the list one raised `invalid syntax: unexpected '*'` at exit 1.
                if self.is_op("*") {
                    return Err(unsupported("unpack", "* in a list display"));
                }
                let first = self.expr()?;
                if self.is_kw("for") {
                    let clauses = self.comp_clauses()?;
                    self.expect_op("]")?;
                    return Ok(Expr::Comp {
                        kind: CompKind::List,
                        elt: Box::new(first),
                        val: None,
                        clauses,
                    });
                }
                let mut items = vec![first];
                while self.eat_op(",") {
                    if self.is_op("]") {
                        break;
                    }
                    items.push(self.expr()?);
                }
                self.expect_op("]")?;
                Ok(Expr::List(items))
            }
            Tok::Op("{") => {
                self.bump();
                // The list and parenthesized twins of this are refused above.
                // `**` is NOT refused: `{**d, 'b': 2}` is dict merging and it
                // already works — only the `*` set-unpacking form does not.
                if self.is_op("*") {
                    return Err(unsupported("unpack", "* in a set display"));
                }
                if self.eat_op("}") {
                    return Ok(Expr::Dict(Vec::new()));
                }
                if self.is_op("**") {
                    return self.dict_unpack_tail(Vec::new());
                }
                let first = self.expr()?;
                if self.eat_op(":") {
                    let v = self.expr()?;
                    if self.is_kw("for") {
                        let clauses = self.comp_clauses()?;
                        self.expect_op("}")?;
                        return Ok(Expr::Comp {
                            kind: CompKind::Dict,
                            elt: Box::new(first),
                            val: Some(Box::new(v)),
                            clauses,
                        });
                    }
                    let mut items = vec![DictItem::Pair(first, v)];
                    while self.eat_op(",") {
                        if self.is_op("}") {
                            break;
                        }
                        if self.eat_op("**") {
                            items.push(DictItem::Unpack(self.expr()?));
                            continue;
                        }
                        let k = self.expr()?;
                        self.expect_op(":")?;
                        items.push(DictItem::Pair(k, self.expr()?));
                    }
                    self.expect_op("}")?;
                    let simple = items.iter().all(|i| matches!(i, DictItem::Pair(..)));
                    return Ok(if simple {
                        Expr::Dict(
                            items
                                .into_iter()
                                .map(|i| match i {
                                    DictItem::Pair(k, v) => (k, v),
                                    _ => unreachable!(),
                                })
                                .collect(),
                        )
                    } else {
                        Expr::DictUnpack(items)
                    });
                }
                // A set display or set comprehension.
                if self.is_kw("for") {
                    let clauses = self.comp_clauses()?;
                    self.expect_op("}")?;
                    return Ok(Expr::Comp {
                        kind: CompKind::Set,
                        elt: Box::new(first),
                        val: None,
                        clauses,
                    });
                }
                let mut items = vec![first];
                while self.eat_op(",") {
                    if self.is_op("}") {
                        break;
                    }
                    items.push(self.expr()?);
                }
                self.expect_op("}")?;
                Ok(Expr::Set(items))
            }
            Tok::Op("...") => {
                self.bump();
                Err(unsupported("ellipsis", "Ellipsis literal"))
            }
            _ => Err(LypningError::syntax(
                self.line(),
                &format!("invalid syntax: unexpected {}", self.describe()),
            )),
        }
    }

    fn dict_unpack_tail(&mut self, mut items: Vec<DictItem>) -> R<Expr> {
        loop {
            if self.is_op("}") {
                break;
            }
            if self.eat_op("**") {
                items.push(DictItem::Unpack(self.expr()?));
            } else {
                let k = self.expr()?;
                self.expect_op(":")?;
                items.push(DictItem::Pair(k, self.expr()?));
            }
            if !self.eat_op(",") {
                break;
            }
        }
        self.expect_op("}")?;
        Ok(Expr::DictUnpack(items))
    }

    /// Adjacent string literals concatenate: `"a" "b"` and `f"a" "b"`.
    fn string_group(&mut self) -> R<Expr> {
        let mut parts: Vec<FPart> = Vec::new();
        let mut plain: Vec<u8> = Vec::new();
        let mut any_f = false;
        let mut any_bytes = false;
        let mut any_str = false;
        loop {
            match self.peek().clone() {
                Tok::Str { value, is_bytes } => {
                    self.bump();
                    if is_bytes {
                        any_bytes = true;
                    } else {
                        any_str = true;
                    }
                    if any_f {
                        parts.push(FPart::Lit(
                            String::from_utf8_lossy(&value).into_owned().into(),
                        ));
                    } else {
                        plain.extend_from_slice(&value);
                    }
                }
                Tok::FStr { raw, raw_prefix } => {
                    self.bump();
                    any_str = true;
                    if !any_f {
                        any_f = true;
                        if !plain.is_empty() {
                            parts.push(FPart::Lit(
                                String::from_utf8_lossy(&plain).into_owned().into(),
                            ));
                            plain.clear();
                        }
                    }
                    parts.extend(parse_fstring(&raw, raw_prefix)?);
                }
                _ => break,
            }
        }
        if any_bytes && any_str {
            return Err(LypningError::syntax(
                self.line(),
                "cannot mix bytes and nonbytes literals",
            ));
        }
        if any_f {
            return Ok(Expr::FString(parts));
        }
        if any_bytes {
            return Ok(Expr::Bytes(Rc::new(plain)));
        }
        match String::from_utf8(plain) {
            Ok(s) => Ok(Expr::Str(s.into())),
            Err(_) => Err(unsupported("token", "non-utf8 string literal")),
        }
    }
}

/// Split an f-string body into literal and replacement parts.
fn parse_fstring(raw: &str, raw_prefix: bool) -> R<Vec<FPart>> {
    let b = raw.as_bytes();
    let mut parts = Vec::new();
    let mut lit = String::new();
    let mut i = 0;
    while i < b.len() {
        match b[i] {
            b'{' if i + 1 < b.len() && b[i + 1] == b'{' => {
                lit.push('{');
                i += 2;
            }
            b'}' if i + 1 < b.len() && b[i + 1] == b'}' => {
                lit.push('}');
                i += 2;
            }
            b'{' => {
                if !lit.is_empty() {
                    let decoded = finish_lit(&lit, raw_prefix)?;
                    parts.push(FPart::Lit(decoded.into()));
                    lit.clear();
                }
                let (expr_src, conv, spec_src, next) = split_field(raw, i + 1)?;
                i = next;
                if expr_src.trim_end().ends_with('=') && !expr_src.trim_end().ends_with("==") {
                    return Err(unsupported("fstring", "self-documenting {x=} field"));
                }
                let mut p = Parser {
                    t: tokenize(expr_src.trim())?,
                    i: 0,
                    depth: 0,
                    chain_ops: 0,
                };
                let e = p.expr_list()?;
                if !matches!(p.peek(), Tok::Newline | Tok::Eof) {
                    return Err(LypningError::syntax(0, "invalid f-string expression"));
                }
                let spec = match spec_src {
                    None => None,
                    Some(s) => {
                        let sub = parse_fstring(&s, raw_prefix)?;
                        Some(Box::new(Expr::FString(sub)))
                    }
                };
                parts.push(FPart::Expr {
                    expr: Box::new(e),
                    conv,
                    spec,
                });
            }
            b'}' => return Err(LypningError::syntax(0, "f-string: single '}' is not allowed")),
            c => {
                lit.push(c as char);
                if c >= 0x80 {
                    // Re-sync onto the character boundary: push the raw bytes
                    // and let the final decode handle UTF-8.
                    lit.pop();
                    let start = i;
                    let mut j = i + 1;
                    while j < b.len() && (b[j] & 0xc0) == 0x80 {
                        j += 1;
                    }
                    lit.push_str(&raw[start..j]);
                    i = j;
                    continue;
                }
                i += 1;
            }
        }
    }
    if !lit.is_empty() {
        parts.push(FPart::Lit(finish_lit(&lit, raw_prefix)?.into()));
    }
    Ok(parts)
}

fn finish_lit(lit: &str, raw_prefix: bool) -> R<String> {
    if raw_prefix {
        return Ok(lit.to_string());
    }
    let bytes = decode_escapes(lit, false, 0)?;
    String::from_utf8(bytes).map_err(|_| unsupported("token", "non-utf8 f-string literal"))
}

/// Find the end of a `{...}` replacement field, honouring nesting, strings and
/// the `!conv` / `:spec` tails. Returns (expr, conv, spec, index after '}').
fn split_field(raw: &str, start: usize) -> R<(String, Option<char>, Option<String>, usize)> {
    let b = raw.as_bytes();
    let mut depth = 0i32;
    let mut i = start;
    let mut quote: Option<u8> = None;
    let mut expr_end = None;
    let mut conv = None;
    let mut spec_start = None;
    while i < b.len() {
        let c = b[i];
        if let Some(q) = quote {
            if c == b'\\' {
                i += 2;
                continue;
            }
            if c == q {
                quote = None;
            }
            i += 1;
            continue;
        }
        match c {
            b'\'' | b'"' => quote = Some(c),
            b'(' | b'[' | b'{' => depth += 1,
            b')' | b']' => depth -= 1,
            b'}' if depth > 0 => depth -= 1,
            b'}' => {
                if expr_end.is_none() {
                    expr_end = Some(i);
                }
                let expr = raw[start..expr_end.unwrap()].to_string();
                let spec = spec_start.map(|s: usize| raw[s..i].to_string());
                return Ok((expr, conv, spec, i + 1));
            }
            b'!' if depth == 0
                && expr_end.is_none()
                && i + 1 < b.len()
                && b[i + 1] != b'='
                && spec_start.is_none() =>
            {
                expr_end = Some(i);
                conv = Some(b[i + 1] as char);
                i += 2;
                continue;
            }
            b':' if depth == 0 && spec_start.is_none() => {
                if expr_end.is_none() {
                    expr_end = Some(i);
                }
                spec_start = Some(i + 1);
            }
            _ => {}
        }
        i += 1;
    }
    Err(LypningError::syntax(0, "f-string: expecting '}'"))
}

fn contains_yield(body: &[Stmt]) -> bool {
    // `yield` is rejected at expression level, so a parsed body cannot contain
    // one; the check stays as a guard for future parser changes.
    let _ = body;
    false
}
