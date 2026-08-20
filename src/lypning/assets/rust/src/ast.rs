//! The subset AST.
//!
//! Names stay as `Rc<str>` rather than resolved slots, deliberately. The
//! measurement that decides it is in the lypning-mp skill §2c: a corpus one-liner
//! is 1.7 ms of which 0.04 ms is the interpreter own code, and 96% of an
//! invocation is the OS spawning the process. Slot resolution optimises the
//! 2% and costs a scope-analysis pass that comprehension and function scoping
//! make easy to get subtly wrong. lypning is fast because it starts fast.

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum BinOp {
    Add,
    Sub,
    Mul,
    Div,
    FloorDiv,
    Mod,
    Pow,
    BitAnd,
    BitOr,
    BitXor,
    LShift,
    RShift,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum CmpOp {
    Lt,
    Le,
    Gt,
    Ge,
    Eq,
    Ne,
    In,
    NotIn,
    Is,
    IsNot,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum UnOp {
    Neg,
    Pos,
    Not,
    Invert,
}

#[derive(Debug, Clone)]
pub enum Expr {
    None,
    True,
    False,
    Int(i64),
    Float(f64),
    Str(std::rc::Rc<str>),
    Bytes(std::rc::Rc<Vec<u8>>),
    Name(std::rc::Rc<str>),
    Tuple(Vec<Expr>),
    List(Vec<Expr>),
    Set(Vec<Expr>),
    Dict(Vec<(Expr, Expr)>),
    /// `{**a, 'k': v}` — a dict display with unpacking.
    DictUnpack(Vec<DictItem>),
    Bin(BinOp, Box<Expr>, Box<Expr>),
    Un(UnOp, Box<Expr>),
    /// Chained comparison: `a < b <= c` is one node, evaluated with the
    /// short-circuit + single-evaluation semantics Python guarantees.
    Compare {
        first: Box<Expr>,
        rest: Vec<(CmpOp, Expr)>,
    },
    BoolAnd(Vec<Expr>),
    BoolOr(Vec<Expr>),
    /// `a if c else b`
    Cond {
        cond: Box<Expr>,
        then: Box<Expr>,
        els: Box<Expr>,
    },
    Attr(Box<Expr>, std::rc::Rc<str>),
    Index(Box<Expr>, Box<Expr>),
    Slice {
        base: Box<Expr>,
        lo: Option<Box<Expr>>,
        hi: Option<Box<Expr>>,
        step: Option<Box<Expr>>,
    },
    Call {
        func: Box<Expr>,
        args: Vec<Expr>,
        /// `*args` positions, by index into `args`.
        star: Vec<usize>,
        kwargs: Vec<(std::rc::Rc<str>, Expr)>,
        /// `**kwargs` expressions.
        dstar: Vec<Expr>,
    },
    Comp {
        kind: CompKind,
        /// element (and value, for a dict comprehension)
        elt: Box<Expr>,
        val: Option<Box<Expr>>,
        clauses: Vec<CompClause>,
    },
    /// `*x` on the left of an assignment, or in a display.
    Starred(Box<Expr>),
    /// `f"..."` — a pre-split join of literal and formatted parts.
    FString(Vec<FPart>),
    Lambda {
        params: std::rc::Rc<Params>,
        body: Box<Expr>,
    },
}

#[derive(Debug, Clone)]
pub enum DictItem {
    Pair(Expr, Expr),
    Unpack(Expr),
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum CompKind {
    List,
    Set,
    Dict,
    Gen,
}

#[derive(Debug, Clone)]
pub struct CompClause {
    pub target: Target,
    pub iter: Expr,
    pub ifs: Vec<Expr>,
}

#[derive(Debug, Clone)]
pub enum FPart {
    Lit(std::rc::Rc<str>),
    Expr {
        expr: Box<Expr>,
        conv: Option<char>,
        /// The format spec, itself possibly an f-string (`{x:{w}}`).
        spec: Option<Box<Expr>>,
    },
}

#[derive(Debug, Clone)]
pub enum Target {
    Name(std::rc::Rc<str>),
    Tuple(Vec<Target>),
    Attr(Expr, std::rc::Rc<str>),
    Index(Expr, Expr),
    Slice {
        base: Expr,
        lo: Option<Expr>,
        hi: Option<Expr>,
    },
    /// `a, *rest = ...`
    Star(Box<Target>),
}

#[derive(Debug, Clone, Default)]
pub struct Params {
    pub names: Vec<std::rc::Rc<str>>,
    pub defaults: Vec<Option<Expr>>,
    /// Index of `*args`, if any.
    pub star: Option<usize>,
    /// Index of `**kwargs`, if any.
    pub dstar: Option<usize>,
}

#[derive(Debug, Clone)]
pub enum Stmt {
    Expr(Expr),
    Assign {
        targets: Vec<Target>,
        value: Expr,
    },
    AugAssign {
        target: Target,
        op: BinOp,
        value: Expr,
    },
    If {
        arms: Vec<(Expr, Vec<Stmt>)>,
        els: Vec<Stmt>,
    },
    For {
        target: Target,
        iter: Expr,
        body: Vec<Stmt>,
        els: Vec<Stmt>,
    },
    While {
        cond: Expr,
        body: Vec<Stmt>,
        els: Vec<Stmt>,
    },
    Break,
    Continue,
    Pass,
    Return(Option<Expr>),
    Assert {
        test: Expr,
        msg: Option<Expr>,
    },
    Raise {
        exc: Option<Expr>,
    },
    Def {
        name: std::rc::Rc<str>,
        params: std::rc::Rc<Params>,
        body: std::rc::Rc<Vec<Stmt>>,
    },
    Try {
        body: Vec<Stmt>,
        handlers: Vec<Handler>,
        els: Vec<Stmt>,
        finally: Vec<Stmt>,
    },
    With {
        items: Vec<(Expr, Option<Target>)>,
        body: Vec<Stmt>,
    },
    Import {
        /// (module path, bound name)
        names: Vec<(std::rc::Rc<str>, std::rc::Rc<str>)>,
    },
    FromImport {
        module: std::rc::Rc<str>,
        names: Vec<(std::rc::Rc<str>, std::rc::Rc<str>)>,
    },
    Del(Vec<Target>),
    Global(Vec<std::rc::Rc<str>>),
}

#[derive(Debug, Clone)]
pub struct Handler {
    /// Exception class names this handler catches; empty means bare `except:`.
    pub kinds: Vec<std::rc::Rc<str>>,
    pub name: Option<std::rc::Rc<str>>,
    pub body: Vec<Stmt>,
}

