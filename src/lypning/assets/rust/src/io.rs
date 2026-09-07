//! Files, streams, and the **commit barrier**.
//!
//! The barrier is what makes a mixture-of-interpreters safe. Routing a program
//! to lypning is only sound if a lypning run that ends in `unsupported` left nothing
//! behind — otherwise the retry on a larger sibling or CPython re-executes the side
//! effects and the file is written twice, or half.
//!
//! So a lypning run is transactional:
//!
//!   * stdout and stderr accumulate in memory and are written once, at a
//!     successful exit;
//!   * file writes accumulate per path and are flushed at the same moment;
//!   * a directory is made for REAL and remembered, and [`rewind`] removes it;
//!   * exit 90 undoes all of it, so the program is observably a no-op.
//!
//! **Two techniques, and the third line is why.** Content can be staged, so it
//! is: nothing reaches the disk until the run commits. A directory cannot —
//! `os.mkdir` has no content to hold back — and modelling one instead would put
//! a second directory tree behind `os.path.isdir`, `glob`'s `readdir`, the
//! `real_dir` a listing resolves symlinks with and every write into it, any one
//! of which could disagree with the disk at exit 0. So the directory is real
//! and the barrier keeps an UNDO LOG. That works for the reason the second line
//! gives: every file the program writes is still staged, so the run's own
//! directories are empty when a refusal arrives, and removing them newest first
//! restores exactly the tree the run started with.
//!
//! **What cannot be taken back** is then a short list, and it is what
//! [`mark_committed`] is for: bytes past `COMMIT_THRESHOLD`, which have already
//! left the process; `os.rmdir` of a directory this run did not make, whose
//! mode, timestamps and ownership `create_dir` cannot restore; and a [`rewind`]
//! that could not remove one. A later `unsupported` is then reported as a hard
//! error rather than a routing signal — naming WHICH of the three happened,
//! through [`commit_reason`], because a diagnostic that guesses is a bug report
//! filed against the wrong mechanism. And a run that cannot be taken back keeps
//! everything it produced: the refusal costs it its routing, never its output.
//! `os.mkdir` was on that list until issue #51 — three capability rounds each
//! met it through a different refusal kind, each worked around it in the
//! ROUTER, and the fourth moved the barrier instead.

use crate::err::{unsupported, LypningError, R};
use crate::host;
use std::cell::RefCell;
use crate::hash::Map;
use std::io::{Read, Write};

/// Past this many buffered bytes the run commits early and gives up its
/// ability to fall back. 8 MiB is far above anything in the corpus.
pub const COMMIT_THRESHOLD: usize = 8 << 20;

#[derive(Clone, Copy, PartialEq)]
pub enum Mode {
    Read,
    Write,
    Append,
}

pub struct FileObj {
    pub path: String,
    pub mode: Mode,
    pub binary: bool,
    pub closed: bool,
    /// Read buffer with the cursor, for `.read()` / `.readline()` / iteration.
    pub data: Vec<u8>,
    pub pos: usize,
    /// What `open(newline=…)` asked this text stream to do with line endings —
    /// one of [`NEWLINE_UNIVERSAL`], [`NEWLINE_RAW`], [`NEWLINE_KEEP_NL`].
    ///
    /// It is the STREAM's flag and not the parser's, which is why `iter::Lines`
    /// is the one place it is read: `newline=''` means a line ends at `\r\n`,
    /// `\n` OR a bare `\r`, and `newline=None` means the same three ends AND
    /// that each of them arrives as `\n`. A file served under either flag while
    /// the stream still split at `\n` alone swallowed every bare CR, and
    /// `readline`, `readlines`, `for line in f`, `seek(0)` and every
    /// `csv.reader` over it went with it. `csv.rs` has no line splitter of its
    /// own to disagree with this one — it reads the file object's lines, the
    /// same ones a `for` loop gets.
    ///
    /// The core neither serves nor stores the mode: `newline=''` refuses there,
    /// exactly as it did before `cap-csv` existed.
    #[cfg(feature = "cap-csv")]
    pub newline_mode: u8,
    /// The write generation [`FileObj::path`] had when this handle opened it.
    ///
    /// A `FileObj`'s `data` is the bytes `open()` read, once and at that
    /// moment; CPython's file object reads the descriptor as it goes. The two
    /// agree until something writes the path under an open handle — and a
    /// `csv.reader` is the only reader here lazy enough to be caught by it, so
    /// it compares this against [`write_gen`] before every row and refuses
    /// rather than yield a record CPython has already replaced.
    ///
    /// A COUNTER and not a flag, because "has this path ever been written" is
    /// the wrong question: `open(p,'w').write(text)` and then
    /// `csv.reader(open(p))` reads the staged bytes through
    /// [`effective_content`] and is not stale at all. "Written SINCE this
    /// handle opened it" is the question, and two numbers answer it.
    #[cfg(feature = "cap-csv")]
    pub write_gen: u32,
    /// `TextIOWrapper`'s `telling`, which `f.tell()` needs and nothing else
    /// does. `__next__` on a TEXT stream clears it — CPython cannot say where a
    /// position is once its read-ahead is in play — and restores it at the EOF
    /// that ends the iteration, or at the next `seek()`. So `for line in f:`
    /// run to the end leaves `tell()` working and a `break` out of it does not,
    /// which is measured (`methods::tell_exact`) rather than reasoned about.
    #[cfg(feature = "cap-csv")]
    pub telling: bool,
}

/// `newline=None` — `\r\n` and a lone `\r` become `\n`.
#[cfg(feature = "cap-csv")]
pub const NEWLINE_UNIVERSAL: u8 = 0;
/// `newline=''` — no translation; a line ends at `\r\n`, `\n` or `\r`.
#[cfg(feature = "cap-csv")]
pub const NEWLINE_RAW: u8 = 1;
/// `newline='\n'` — no translation; a line ends only at `\n`.
#[cfg(feature = "cap-csv")]
pub const NEWLINE_KEEP_NL: u8 = 2;

#[derive(Default)]
pub struct Pending {
    /// path -> (bytes, append?) staged until commit
    pub files: Map<String, (Vec<u8>, bool)>,
    /// Ordered so the flush reproduces the program's own write order.
    pub order: Vec<String>,
}

thread_local! {
    static OUT: RefCell<Vec<u8>> = const { RefCell::new(Vec::new()) };
    static ERRBUF: RefCell<Vec<u8>> = const { RefCell::new(Vec::new()) };
    static PENDING: RefCell<Pending> = RefCell::new(Pending::default());
    static COMMITTED: RefCell<bool> = const { RefCell::new(false) };
    /// WHY it committed — see [`commit_reason`].
    static COMMIT_WHY: RefCell<&'static str> = const { RefCell::new("") };
    /// Every directory this run has created, canonicalised, in creation order.
    /// The barrier's UNDO log — see [`make_dir`] and [`rewind`].
    static MADE: RefCell<Vec<std::path::PathBuf>> = const { RefCell::new(Vec::new()) };
    static DELETED: RefCell<crate::hash::Set<String>> =
        RefCell::new(crate::hash::Set::with_hasher(crate::hash::BuildFnv));
}

/// The bytes have left the process.
pub const WHY_FLUSHED: &str = "output was already flushed";
/// [`remove_dir`] took a directory this run did not make.
pub const WHY_FOREIGN_RMDIR: &str = "os.rmdir removed a directory this run did not make";
/// [`rewind`] could not give a directory back.
pub const WHY_DIR_STUCK: &str = "a directory this run created could not be removed";

/// Record that something irreversible happened outside the staging area, and
/// WHAT it was.
///
/// Three callers, and they are the whole list: the early flush past
/// [`COMMIT_THRESHOLD`], where bytes have left the process; [`remove_dir`] of a
/// directory this run did not make, whose mode, timestamps and ownership are
/// gone with it; and a [`rewind`] that could not remove one. All three are
/// effects nothing can give back, so the run stops claiming it can be re-run.
///
/// The reason is an argument because the refusal line quotes it — `reached
/// after {why}, so the run cannot be routed onward` — and a bool cannot say
/// which of three things happened. It said "output was already flushed" after
/// an `os.rmdir` that had flushed nothing, which is a diagnostic pointing at a
/// cause that never occurred. The FIRST reason is the one kept: it is where the
/// run stopped being reversible, and anything after it is a consequence.
///
/// `os.mkdir` used to be on this list, and it is the one this barrier now takes
/// back instead — see [`make_dir`].
pub fn mark_committed(why: &'static str) {
    COMMITTED.with(|c| *c.borrow_mut() = true);
    COMMIT_WHY.with(|w| {
        let mut w = w.borrow_mut();
        if w.is_empty() {
            *w = why;
        }
    });
}

pub fn is_committed() -> bool {
    COMMITTED.with(|c| *c.borrow())
}

/// What made this run irreversible, or `""` while it can still be taken back.
///
/// The ONE source of the refusal line's `reached after …` clause, read by
/// `main.rs::finish` and `embed.rs::finish` alike: two renderings of one reason
/// are two things that can drift, and this one already had.
pub fn commit_reason() -> &'static str {
    COMMIT_WHY.with(|w| *w.borrow())
}

pub fn write_out(b: &[u8]) -> R<()> {
    OUT.with(|o| o.borrow_mut().extend_from_slice(b));
    maybe_commit()
}

pub fn write_err(b: &[u8]) -> R<()> {
    ERRBUF.with(|o| o.borrow_mut().extend_from_slice(b));
    maybe_commit()
}

fn stream_len() -> usize {
    OUT.with(|o| o.borrow().len()) + ERRBUF.with(|o| o.borrow().len())
}

fn staged_len() -> usize {
    PENDING.with(|p| p.borrow().files.values().map(|(b, _)| b.len()).sum::<usize>())
}

fn buffered_len() -> usize {
    stream_len() + staged_len()
}

/// The ceiling, and it means something different in each of the two shapes.
///
/// **In the CLI** the buffer's only exit is fd 1, so the choice past
/// `COMMIT_THRESHOLD` is between flushing early — losing the ability to fall
/// onward — and growing without bound. It flushes, and says so by committing.
///
/// **Embedded** the output has not left anything yet: the host is handed the
/// bytes when the run returns, so a refusal here costs the caller nothing and
/// stays routable, which is strictly better than filling their address space.
/// So the two halves separate. Captured streams refuse at the host's limit;
/// staged FILE writes — the one thing that is a real side effect either way —
/// keep the original early-commit behaviour, because a host that let the
/// program open files asked for those bytes to reach the disk.
fn maybe_commit() -> R<()> {
    if host::embedded() {
        let limit = host::output_limit();
        if limit > 0 && stream_len() > limit {
            return Err(unsupported(
                "output",
                &format!("captured output passed the host's limit of {limit} bytes"),
            ));
        }
        if !is_committed() && staged_len() > COMMIT_THRESHOLD {
            commit()?;
            mark_committed(WHY_FLUSHED);
        }
        return Ok(());
    }
    if !is_committed() && buffered_len() > COMMIT_THRESHOLD {
        commit()?;
        mark_committed(WHY_FLUSHED);
    }
    Ok(())
}

/// Flush everything staged. Called once, at a successful exit.
///
/// The undo log goes first: from the moment a commit starts, the directories
/// this run made are permanent whatever happens to the writes behind them, and
/// a later [`rewind`] must not reach back for them.
pub fn commit() -> R<()> {
    MADE.with(|m| m.borrow_mut().clear());
    DELETED.with(|d| -> R<()> {
        for path in d.borrow_mut().drain() {
            match std::fs::remove_file(&path) {
                Ok(()) => {}
                Err(e) if e.kind() == std::io::ErrorKind::NotFound => {}
                Err(e) => return Err(os_error(&path, &e)),
            }
        }
        Ok(())
    })?;
    PENDING.with(|p| -> R<()> {
        let mut p = p.borrow_mut();
        let order = std::mem::take(&mut p.order);
        for path in order {
            if let Some((bytes, append)) = p.files.remove(&path) {
                let r = if append {
                    std::fs::OpenOptions::new()
                        .create(true)
                        .append(true)
                        .open(&path)
                        .and_then(|mut f| f.write_all(&bytes))
                } else {
                    std::fs::write(&path, &bytes)
                };
                r.map_err(|e| os_error(&path, &e))?;
            }
        }
        Ok(())
    })?;
    if host::embedded() {
        // The host's two buffers ARE the destination. Draining them into the
        // process's stdout would write a library caller's program output onto
        // whatever fd 1 happens to be — the single worst thing an embedded
        // runtime can do to an application.
        return Ok(());
    }
    OUT.with(|o| {
        let mut o = o.borrow_mut();
        if !o.is_empty() {
            let _ = std::io::stdout().write_all(&o);
            let _ = std::io::stdout().flush();
            o.clear();
        }
    });
    ERRBUF.with(|o| {
        let mut o = o.borrow_mut();
        if !o.is_empty() {
            let _ = std::io::stderr().write_all(&o);
            let _ = std::io::stderr().flush();
            o.clear();
        }
    });
    Ok(())
}

/// Put the disk and the streams back where the run found them, and say whether
/// that worked. **The question every refusal asks**, and the answer is what
/// decides between exit 90 and exit 1.
///
/// [`discard`] alone is not that answer, because a directory is not staged: it
/// is made for real, so that `os.path.isdir`, `glob`, a write into it and the
/// `readdir` under it all see the same filesystem CPython would — no second
/// model of a directory tree to disagree with the first. What makes it
/// reversible is not staging but the undo log: the run's own directories are
/// empty (every file in them is still staged), so removing them in reverse
/// order restores exactly the tree the run started with.
///
/// `false` means a directory would not go — non-empty because something
/// outside this process filled it, or unreadable. Then the run really did
/// leave something behind, it is [`mark_committed`], and the caller must report
/// the refusal as its own error rather than a routing signal.
///
/// **A failed undo costs the program nothing it already had.** The removals go
/// first and [`discard`] only after every one of them succeeded, because
/// `discard` throws away the staged stdout, the staged writes and the staged
/// deletes — and the caller answers a `false` by COMMITTING, which is then a
/// commit of nothing. Discarding before the answer was known handed back an
/// exit 1 with empty stdout where the barrier this replaced handed back the
/// program's own output at the same exit 1: a fix that made the unfixable case
/// worse than it was. Anything already removed on the way to a failure is put
/// back, for the same reason and by the same rule the rest of this module
/// follows: a run that has committed KEEPS its directories.
pub fn rewind() -> bool {
    let made = MADE.with(|m| std::mem::take(&mut *m.borrow_mut()));
    let mut undone: Vec<std::path::PathBuf> = Vec::new();
    let mut clean = true;
    for d in made.iter().rev() {
        match std::fs::remove_dir(d) {
            Ok(()) => undone.push(d.clone()),
            // Already gone is nothing left behind, which is all this asks.
            Err(e) if e.kind() == std::io::ErrorKind::NotFound => {}
            Err(_) => {
                clean = false;
                break;
            }
        }
    }
    if !clean {
        // Shallowest last out, so a re-created parent exists before its child.
        // These are the run's OWN directories, made by `create_dir` and still
        // empty — every file written into them is staged — so putting them back
        // reproduces exactly the tree the program built, which is the tree the
        // commit below is about to flush its writes into.
        for d in undone.iter().rev() {
            let _ = std::fs::create_dir(d);
        }
        MADE.with(|m| *m.borrow_mut() = made);
        mark_committed(WHY_DIR_STUCK);
        return false;
    }
    discard();
    true
}

/// Throw away everything staged — the run is being routed onward.
pub fn discard() {
    OUT.with(|o| o.borrow_mut().clear());
    ERRBUF.with(|o| o.borrow_mut().clear());
    PENDING.with(|p| {
        let mut p = p.borrow_mut();
        p.files.clear();
        p.order.clear();
    });
    DELETED.with(|d| d.borrow_mut().clear());
}

pub fn os_error(path: &str, e: &std::io::Error) -> LypningError {
    let (kind, errno, msg) = match e.kind() {
        std::io::ErrorKind::NotFound => ("FileNotFoundError", 2, "No such file or directory"),
        std::io::ErrorKind::PermissionDenied => ("PermissionError", 13, "Permission denied"),
        std::io::ErrorKind::AlreadyExists => ("FileExistsError", 17, "File exists"),
        _ => ("OSError", e.raw_os_error().unwrap_or(0), "OS error"),
    };
    let detail = if kind == "OSError" {
        e.to_string()
    } else {
        msg.to_string()
    };
    LypningError::exc(
        match kind {
            "FileNotFoundError" => "FileNotFoundError",
            "PermissionError" => "PermissionError",
            "FileExistsError" => "FileExistsError",
            _ => "OSError",
        },
        format!("[Errno {errno}] {detail}: '{path}'"),
    )
}

/// The effective content of a path, accounting for writes this run has staged
/// but not yet committed.
///
/// Without this the commit barrier would break `open(p,'w').write(x)` followed
/// by `open(p).read()` — the program would read the file as it was BEFORE its
/// own write. The barrier has to be invisible to the program and visible only
/// to the dispatcher; that is what makes it a safety mechanism rather than a
/// behaviour change.
pub fn effective_content(path: &str) -> R<Option<Vec<u8>>> {
    let staged = PENDING.with(|p| p.borrow().files.get(path).cloned());
    match staged {
        None => Ok(None),
        Some((buf, append)) => {
            if append {
                require_regular_file(path)?;
                let mut base = std::fs::read(path).unwrap_or_default();
                base.extend_from_slice(&buf);
                Ok(Some(base))
            } else {
                Ok(Some(buf))
            }
        }
    }
}

/// A read this engine must not START unless it can finish.
///
/// Every path this module reads it reads WHOLE — `open()` snapshots the bytes
/// and `.read(n)` then slices the snapshot — so `open('/dev/zero').read(1)`
/// asks `std::fs::read` for an infinite file and the process never comes back.
/// CPython streams and answers `b'\x00'`. A hang is the one outcome worse than
/// a wrong answer here: a wrong answer is still an answer, and the dispatcher
/// cannot reach the next tier from inside a hang — no exit code, no refusal
/// line, nothing to route on. So only a REGULAR file is read, and everything
/// else refuses before the read starts.
///
/// A directory is let through deliberately: `std::fs::read` fails on it at
/// once, and the error it raises is the one this engine already answers with.
/// A path that cannot be stat'ed is let through too, so that the caller's own
/// read reports it and the exception text stays the one it already produced.
pub fn require_regular_file(path: &str) -> R<()> {
    match std::fs::metadata(path) {
        Ok(md) if md.is_file() || md.is_dir() => Ok(()),
        Ok(_) => Err(unsupported(
            "open-special",
            &format!(
                "reading '{path}', which is not a regular file — this engine reads a path whole \
                 and a device or a pipe has no end"
            ),
        )),
        Err(_) => Ok(()),
    }
}

/// Paths this run has deleted or renamed away but not yet committed.
pub fn is_staged_deleted(path: &str) -> bool {
    DELETED.with(|d| d.borrow().contains(path))
}

/// Is the barrier holding anything back at all?
///
/// The one question a DIRECTORY LISTING has to ask before it does any work.
/// `path_exists` and `effective_content` are handed one path and look it up by
/// the spelling the program used; a listing is handed a DIRECTORY and has to
/// decide which staged spelling names an entry of it, which costs a `realpath`
/// per candidate. This is what keeps that cost off every run that never wrote
/// anything — which is almost all of them.
#[cfg(feature = "cap-glob")]
pub fn staging_active() -> bool {
    PENDING.with(|p| !p.borrow().files.is_empty()) || DELETED.with(|d| !d.borrow().is_empty())
}

/// Every path this run has written but not committed, and every path it has
/// removed, both in the program's own spelling. `glob.rs` splits each one and
/// resolves its directory to decide where the entry belongs.
#[cfg(feature = "cap-glob")]
pub fn staged_write_paths() -> Vec<String> {
    PENDING.with(|p| p.borrow().order.clone())
}

#[cfg(feature = "cap-glob")]
pub fn staged_delete_paths() -> Vec<String> {
    DELETED.with(|d| d.borrow().iter().cloned().collect())
}

/// A delete is deliberately NOT a [`note_write`]: unlinking a path does not
/// change the bytes an already-open handle reads — CPython's descriptor holds
/// the inode open and this engine's `FileObj` holds a copy — so a `csv.reader`
/// over a file the program then removes goes on yielding on both, and refusing
/// it would cost a spawn for a divergence that does not exist.
pub fn stage_delete(path: &str) {
    PENDING.with(|p| {
        let mut p = p.borrow_mut();
        p.files.remove(path);
        p.order.retain(|x| x != path);
    });
    DELETED.with(|d| {
        d.borrow_mut().insert(path.to_string());
    });
}

pub fn stage_write(path: &str, bytes: Vec<u8>) {
    note_write(path);
    DELETED.with(|d| {
        d.borrow_mut().remove(path);
    });
    PENDING.with(|p| {
        let mut p = p.borrow_mut();
        if !p.files.contains_key(path) {
            p.order.push(path.to_string());
        }
        p.files.insert(path.to_string(), (bytes, false));
    });
}

/// `os.mkdir`, `os.makedirs` and `Path.mkdir` — one implementation, because a
/// second one is how a run stops being reversible without saying so.
///
/// The directory is made FOR REAL and remembered, rather than staged. Staging
/// it would mean a second model of a directory tree — one `os.path.isdir`,
/// `glob`'s `readdir`, the `real_dir` a listing resolves symlinks with, and
/// every write into it would have to consult and agree with — and a model that
/// disagrees with the disk is a wrong answer at exit 0, which is the worst
/// thing this repository can produce. Making it for real means there is nothing
/// to disagree: every observer sees what CPython would see. What the barrier
/// keeps is the ability to take it back, and it can, because the directory is
/// still empty — every file the program wrote into it is staged in `PENDING`
/// until the run commits. [`rewind`] removes them newest first.
///
/// The existence test is [`path_exists`] and not the disk, so a path the run
/// has staged a WRITE to is already taken: `open('F','w'); os.mkdir('F')` is
/// CPython's `FileExistsError` rather than a directory made on top of a file
/// that has not landed yet.
pub fn make_dir(path: &str, parents: bool, exist_ok: bool) -> R<()> {
    let p = std::path::Path::new(path);
    if parents {
        // Which ancestors are missing, so that only the ones this run actually
        // creates go in the undo log. `create_dir_all` cannot say.
        let mut missing: Vec<std::path::PathBuf> = Vec::new();
        let mut cur = p.parent();
        while let Some(a) = cur {
            if a.as_os_str().is_empty() || path_exists(&a.to_string_lossy()) {
                break;
            }
            missing.push(a.to_path_buf());
            cur = a.parent();
        }
        for a in missing.iter().rev() {
            staged_delete_blocks(&a.to_string_lossy())?;
            match std::fs::create_dir(a) {
                Ok(()) => note_made(a),
                // Already there: `os.makedirs` makes every parent with
                // `exist_ok=True`, whatever the leaf was asked for.
                Err(e) if e.kind() == std::io::ErrorKind::AlreadyExists => {}
                Err(e) => return Err(os_error(&a.to_string_lossy(), &e)),
            }
        }
    }
    if path_exists(path) {
        // `exist_ok=True` forgives an existing DIRECTORY and nothing else,
        // which is CPython's `if not exist_ok or not path.isdir(name): raise`.
        if exist_ok && p.is_dir() {
            return Ok(());
        }
        return Err(LypningError::exc(
            "FileExistsError",
            format!("[Errno 17] File exists: '{path}'"),
        ));
    }
    staged_delete_blocks(path)?;
    match std::fs::create_dir(p) {
        Ok(()) => {
            note_made(p);
            Ok(())
        }
        Err(e) if exist_ok && e.kind() == std::io::ErrorKind::AlreadyExists && p.is_dir() => Ok(()),
        Err(e) => Err(os_error(path, &e)),
    }
}

/// The one place `make_dir`'s two layers can disagree, and it is refused rather
/// than decided.
///
/// The existence test is [`path_exists`], which merges staging; the creation is
/// `std::fs::create_dir`, which can only ask the disk. A path this run has
/// staged a DELETE of is free to the first and taken by the second, and BOTH
/// orders of that disagreement were a wrong answer at the wrong exit code:
///
/// ```text
///   touch F;  os.remove('F'); os.mkdir('F')   FileExistsError, exit 1
///   open('F','w'); os.remove('F'); os.mkdir('F')
///                                             'ok' AND a PermissionError, exit 1
/// ```
///
/// where CPython removes the file, makes the directory and exits 0 for both.
/// The second is the worse one and shows why the disagreement cannot be
/// patched at either end: the disk lets `create_dir` through, and the staged
/// delete then arrives at [`commit`] to `remove_file` a path that has become a
/// directory.
///
/// Neither layer can serve it alone — honouring staging needs the delete to
/// have HAPPENED, which the barrier will not do before a commit, and honouring
/// the disk is a wrong answer — so the engine refuses and CPython does both for
/// real, one spawn later. The test is the staging layer's alone, so the two
/// halves of a `mkdir` now consult exactly one.
fn staged_delete_blocks(path: &str) -> R<()> {
    if is_staged_deleted(path) {
        return Err(unsupported(
            "mkdir",
            &format!(
                "mkdir('{path}') over a path this run removed but has not committed — the \
                 staging layer says it is free and the disk has not been told yet"
            ),
        ));
    }
    Ok(())
}

/// Remember a directory this run made, by the path the KERNEL knows it as.
///
/// Canonicalised because the undo has to find the same directory the program
/// named, and `os.mkdir('D')` followed by `os.rmdir('./D')` is one directory
/// under two spellings. It resolves here and not at rewind time because here
/// the directory still exists.
fn note_made(p: &std::path::Path) {
    let real = std::fs::canonicalize(p).unwrap_or_else(|_| p.to_path_buf());
    MADE.with(|m| m.borrow_mut().push(real));
}

/// `os.rmdir`, and the one directory removal the barrier can take back.
///
/// Removing a directory this run MADE is a no-op over the whole run — it did
/// not exist before and does not exist after — so the undo log simply forgets
/// it and the run stays routable. Removing anyone else's is irreversible in a
/// way `create_dir` cannot fake: the mode, the timestamps and the ownership are
/// gone. That one commits.
pub fn remove_dir(path: &str) -> R<()> {
    let real = std::fs::canonicalize(path).ok();
    std::fs::remove_dir(path).map_err(|e| os_error(path, &e))?;
    let ours = real
        .map(|r| {
            MADE.with(|m| {
                let mut m = m.borrow_mut();
                match m.iter().rposition(|q| *q == r) {
                    Some(i) => {
                        m.remove(i);
                        true
                    }
                    None => false,
                }
            })
        })
        .unwrap_or(false);
    if !ours {
        mark_committed(WHY_FOREIGN_RMDIR);
    }
    Ok(())
}

/// Does the path exist, as the PROGRAM sees it?
pub fn path_exists(path: &str) -> bool {
    if is_staged_deleted(path) {
        return false;
    }
    PENDING.with(|p| p.borrow().files.contains_key(path)) || std::path::Path::new(path).exists()
}

pub fn open_file(path: &str, mode: &str, binary: bool) -> R<FileObj> {
    if !host::filesystem_allowed() {
        // A denial, not a lie. Reporting "no such file" would hand the program
        // a WRONG ANSWER at exit 0, which is the one outcome the whole refusal
        // contract exists to prevent; refusing leaves the host free to run the
        // program somewhere it is allowed to open files.
        return Err(unsupported(
            "sandbox",
            &format!("open('{path}') with the filesystem denied"),
        ));
    }
    let m = match mode {
        "r" => Mode::Read,
        "w" => Mode::Write,
        "a" => Mode::Append,
        other => {
            return Err(unsupported(
                "open-mode",
                &format!("open() mode '{other}'"),
            ))
        }
    };
    let data = if m == Mode::Read {
        match effective_content(path)? {
            Some(d) => d,
            None => {
                if is_staged_deleted(path) {
                    return Err(LypningError::exc(
                        "FileNotFoundError",
                        format!("[Errno 2] No such file or directory: '{path}'"),
                    ));
                }
                require_regular_file(path)?;
                std::fs::read(path).map_err(|e| os_error(path, &e))?
            }
        }
    } else {
        // `open(p,'w')` TRUNCATES and `open(p,'a')` moves the end, and both are
        // writes an open read handle over the same path cannot see.
        note_write(path);
        // Staging the write means the file is not truncated until commit; that
        // is intentional, and it is also what makes `open(p,'w')` reversible.
        DELETED.with(|d| {
            d.borrow_mut().remove(path);
        });
        PENDING.with(|p| {
            let mut p = p.borrow_mut();
            if !p.files.contains_key(path) {
                p.files.insert(path.to_string(), (Vec::new(), m == Mode::Append));
                p.order.push(path.to_string());
            }
        });
        Vec::new()
    };
    Ok(FileObj {
        path: path.to_string(),
        mode: m,
        binary,
        closed: false,
        data,
        pos: 0,
        #[cfg(feature = "cap-csv")]
        newline_mode: NEWLINE_UNIVERSAL,
        #[cfg(feature = "cap-csv")]
        write_gen: write_gen(path),
        #[cfg(feature = "cap-csv")]
        telling: true,
    })
}

// Every path this run has staged a write to, and how many times.
//
// The reader that reads it is `csv.rs`, once per row, through [`write_gen`]
// and `FileObj::write_gen`. Five other guards used to stand here — a drained
// flag on the stream, a second one for `sys.stdin`, a registry pairing every
// live reader with its file, and the two events (`close`, `write`) the file
// had to push back at a reader that could not see them. All five existed
// because the reader was EAGER: it took the whole stream at construction, so
// the rows were right and the MOMENT was not, and every path back to the
// stream had to be told. A lazy reader pulls its lines from the file object
// itself, so the moment is the file's own and there is nothing left to tell:
// a closed file raises from `Iter::Lines`, `f.read()` after `next(r)` returns
// what is left because the reader only took what it yielded, and `sys.stdin`
// keeps one cursor for `input()`, `for line in sys.stdin` and the reader
// alike.
//
// This one is not a csv guard at all. It is the FILE object's own divergence
// — `data` is a snapshot, CPython's is a descriptor — and csv declines to add
// to it rather than closing it: `open(p).read()` after a write to `p` still
// answers from the snapshot, on both variants, as it did before this
// capability existed.
#[cfg(feature = "cap-csv")]
thread_local! {
    static WRITE_GEN: RefCell<Map<String, u32>> = RefCell::new(Map::default());
}

/// Note that `path` has been written. Called from every staging entry point,
/// so `Path.write_text` and `os.remove` count as much as `f.write`.
#[cfg(feature = "cap-csv")]
pub fn note_write(path: &str) {
    WRITE_GEN.with(|g| {
        let mut g = g.borrow_mut();
        // The lookup before the insert is not a style choice: this runs on
        // every `f.write()`, and `path.to_string()` on each of them would be an
        // allocation per line written for the whole of a write loop.
        if let Some(n) = g.get_mut(path) {
            *n += 1;
            return;
        }
        g.insert(path.to_string(), 1);
    });
}

#[cfg(feature = "cap-csv")]
pub fn write_gen(path: &str) -> u32 {
    WRITE_GEN.with(|g| g.borrow().get(path).copied().unwrap_or(0))
}

#[cfg(not(feature = "cap-csv"))]
pub fn note_write(_path: &str) {}

pub fn file_write(f: &FileObj, bytes: &[u8]) -> R<usize> {
    if f.closed {
        return Err(LypningError::exc(
            "ValueError",
            "I/O operation on closed file.",
        ));
    }
    if f.mode == Mode::Read {
        return Err(LypningError::exc(
            "UnsupportedOperation",
            "not writable",
        ));
    }
    note_write(&f.path);
    PENDING.with(|p| {
        let mut p = p.borrow_mut();
        if let Some((buf, _)) = p.files.get_mut(&f.path) {
            buf.extend_from_slice(bytes);
        }
    });
    maybe_commit()?;
    Ok(bytes.len())
}

// ---- stdin ----------------------------------------------------------------

thread_local! {
    static STDIN: RefCell<Option<Vec<u8>>> = const { RefCell::new(None) };
    static STDIN_POS: RefCell<usize> = const { RefCell::new(0) };
}

/// Read all of stdin, once. Reading stdin is a side effect that cannot be
/// undone (the bytes are consumed), so `stdin_consumed()` reports it and the
/// dispatcher replays the captured bytes rather than re-reading the pipe.
pub fn stdin_all() -> R<Vec<u8>> {
    stdin_fill()?;
    STDIN.with(|s| Ok(s.borrow().as_ref().unwrap().clone()))
}

/// Make sure stdin has been read, and return nothing.
///
/// The half of [`stdin_all`] that is not a copy. It exists because
/// [`stdin_line`] used to call `stdin_all` — which ends in `.clone()` of the
/// WHOLE captured input — once per line, making `for line in sys.stdin`
/// quadratic in the size of its input. Measured on this container before the
/// split, ~22-byte lines, min of 3:
///
/// ```text
///   1,000 lines     2.96 ms        8,000 lines    583.68 ms
///   2,000 lines    49.93 ms       16,000 lines  2,299.99 ms
///   4,000 lines   182.75 ms
/// ```
///
/// Roughly four times the cost per doubling, against CPython's 3.39 ms at
/// sixteen thousand. `modules.rs` calls `stdin -> transform -> stdout` the
/// corpus's largest single cluster, so this was the hottest real path there is.
///
/// `stdin_all` keeps its copy: `sys.stdin.read()` and the dispatcher's replay
/// (`stdin_consumed`, which decides whether a refusal after reading stdin can
/// re-exec) both need to own the bytes, and both pay for it once.
fn stdin_fill() -> R<()> {
    STDIN.with(|s| {
        let mut s = s.borrow_mut();
        if s.is_none() {
            let mut buf = Vec::new();
            std::io::stdin()
                .read_to_end(&mut buf)
                .map_err(|e| os_error("<stdin>", &e))?;
            *s = Some(buf);
        }
        Ok(())
    })
}

pub fn stdin_consumed() -> Option<Vec<u8>> {
    STDIN.with(|s| s.borrow().clone())
}

/// Read stdin now, on the DISPATCHER's behalf, so that [`stdin_consumed`]
/// has the bytes to replay to every rung of the chain. `main.rs` calls this
/// before forking an intermediate rung whose stdin would otherwise be the
/// inherited pipe — which that child may drain and then refuse, leaving the
/// next rung an empty stream this process never saw. `false` if stdin could
/// not be read (closed, or no fd 0); the caller then inherits, as before.
pub fn stdin_preload() -> bool {
    stdin_fill().is_ok()
}

pub fn stdin_rest() -> R<Vec<u8>> {
    stdin_fill()?;
    // Sliced inside the borrow. The cursor lives in a different thread_local,
    // so reading and writing it here does not overlap this one.
    STDIN.with(|s| {
        let b = s.borrow();
        let all = b.as_ref().unwrap();
        let pos = STDIN_POS.with(|p| *p.borrow()).min(all.len());
        STDIN_POS.with(|p| *p.borrow_mut() = all.len());
        Ok(all[pos..].to_vec())
    })
}

pub fn stdin_line() -> R<Option<Vec<u8>>> {
    stdin_fill()?;
    STDIN.with(|s| {
        let b = s.borrow();
        let all = b.as_ref().unwrap();
        let pos = STDIN_POS.with(|p| *p.borrow());
        if pos >= all.len() {
            return Ok(None);
        }
        let end = match all[pos..].iter().position(|c| *c == b'\n') {
            Some(i) => pos + i + 1,
            None => all.len(),
        };
        STDIN_POS.with(|p| *p.borrow_mut() = end);
        // The only copy that is left, and it is one line long.
        Ok(Some(all[pos..end].to_vec()))
    })
}

// ---- the embedded seam -----------------------------------------------------
//
// Everything above is written for a process that runs one program and exits,
// which is why the state is thread_local and nothing ever resets it. A library
// runs a second program in the same thread, so the same state has to be
// returnable to its starting position — and a leftover byte from the previous
// run would surface as output the current program never printed.

/// Take the captured stdout, leaving the buffer empty.
pub fn take_out() -> Vec<u8> {
    OUT.with(|o| std::mem::take(&mut *o.borrow_mut()))
}

/// Take the captured stderr, leaving the buffer empty.
pub fn take_err() -> Vec<u8> {
    ERRBUF.with(|o| std::mem::take(&mut *o.borrow_mut()))
}

/// Hand the program its stdin, so `stdin_all` never reaches the host's fd 0.
///
/// `None` means an empty stream, and that is the default an embedded run gets
/// rather than the process's: a library call that blocked reading a terminal
/// the host never wrote to would be indistinguishable from a hang.
pub fn set_stdin(bytes: Option<Vec<u8>>) {
    STDIN.with(|s| *s.borrow_mut() = Some(bytes.unwrap_or_default()));
    STDIN_POS.with(|p| *p.borrow_mut() = 0);
}

/// Return every thread_local in this module to the state a fresh process has.
///
/// Called on the way IN to an embedded run, not on the way out: a run that
/// ended in a panic cannot be trusted to have cleaned up after itself, and the
/// next caller is the one who would pay for it.
pub fn reset() {
    discard();
    // CLEARED, never rewound: a fresh run inherits no undo log, and the
    // previous run's directories — if it committed — are the host's now.
    MADE.with(|m| m.borrow_mut().clear());
    COMMITTED.with(|c| *c.borrow_mut() = false);
    COMMIT_WHY.with(|w| *w.borrow_mut() = "");
    STDIN.with(|s| *s.borrow_mut() = None);
    STDIN_POS.with(|p| *p.borrow_mut() = 0);
    #[cfg(feature = "cap-csv")]
    WRITE_GEN.with(|g| g.borrow_mut().clear());
}

#[cfg(test)]
mod tests {
    use super::*;

    fn scratch(tag: &str) -> std::path::PathBuf {
        let d = std::env::temp_dir().join(format!("lypning-barrier-{tag}"));
        let _ = std::fs::remove_dir_all(&d);
        std::fs::create_dir_all(&d).unwrap();
        std::fs::canonicalize(&d).unwrap()
    }

    #[test]
    fn rewind_removes_what_the_run_made_and_nothing_else() {
        let d = scratch("made");
        let keep = d.join("keep");
        std::fs::create_dir(&keep).unwrap();
        let deep = d.join("a").join("b").join("c");
        make_dir(deep.to_str().unwrap(), true, false).unwrap();
        assert!(deep.is_dir());
        assert!(rewind(), "an empty tree this run made is removable");
        assert!(!d.join("a").exists(), "every level the run made goes back");
        assert!(keep.is_dir(), "a directory the run did not make must survive");
        assert!(!is_committed());
        let _ = std::fs::remove_dir_all(&d);
    }

    #[test]
    fn a_directory_under_one_that_already_existed_is_the_only_thing_removed() {
        let d = scratch("partial");
        let a = d.join("a");
        std::fs::create_dir(&a).unwrap();
        make_dir(a.join("b").join("c").to_str().unwrap(), true, false).unwrap();
        assert!(rewind());
        assert!(a.is_dir(), "the pre-existing prefix is not the run's to remove");
        assert!(!a.join("b").exists());
        let _ = std::fs::remove_dir_all(&d);
    }

    /// The `false` arm, and the only actor that can produce it.
    ///
    /// Inside one run it is unreachable by construction: every write the engine
    /// serves is staged in `PENDING` until commit, so a directory the run made
    /// is still empty when the refusal arrives. It takes a second process — or
    /// a permission change — to leave something in it, and here this test is
    /// that process. What must hold is that the barrier NOTICES: `rewind` says
    /// `false`, the run commits, and `main.rs` turns the refusal into the
    /// program's own exit 1 rather than a 90 the chain would act on.
    #[test]
    fn a_directory_something_else_filled_is_reported_rather_than_forced() {
        let d = scratch("filled");
        let made = d.join("D");
        make_dir(made.to_str().unwrap(), false, false).unwrap();
        std::fs::write(made.join("planted"), b"not ours").unwrap();
        assert!(!rewind(), "a non-empty directory is a side effect left behind");
        assert!(is_committed(), "and the run must stop claiming it can be re-run");
        assert!(made.is_dir(), "nothing is forced: the plant is untouched");
        let _ = std::fs::remove_dir_all(&d);
    }

    #[test]
    fn a_committed_run_keeps_its_directories() {
        let d = scratch("committed");
        let made = d.join("D");
        make_dir(made.to_str().unwrap(), false, false).unwrap();
        commit().unwrap();
        assert!(rewind(), "there is nothing left to take back");
        assert!(made.is_dir(), "a commit makes the directory the caller's");
        let _ = std::fs::remove_dir_all(&d);
    }

    #[test]
    fn rmdir_of_this_runs_own_directory_stays_routable() {
        let d = scratch("rmdir");
        let mine = d.join("mine");
        let theirs = d.join("theirs");
        std::fs::create_dir(&theirs).unwrap();
        make_dir(mine.to_str().unwrap(), false, false).unwrap();
        remove_dir(mine.to_str().unwrap()).unwrap();
        assert!(!is_committed(), "made and unmade is a no-op over the run");
        remove_dir(theirs.to_str().unwrap()).unwrap();
        assert!(is_committed(), "someone else's directory cannot be put back");
        let _ = std::fs::remove_dir_all(&d);
    }

    /// The regression the fix introduced, and the shape that closes it.
    ///
    /// A `rewind` that cannot finish must cost the program its ROUTING and
    /// nothing else. It used to `discard()` first, so the answer `false` — the
    /// answer that makes the caller commit — arrived with nothing left to
    /// commit: exit 1 with an empty stdout, where the barrier this replaced
    /// gave exit 1 with the program's own output.
    #[test]
    fn a_failed_rewind_keeps_everything_the_run_produced() {
        let d = scratch("kept");
        let made = d.join("D");
        make_dir(made.to_str().unwrap(), false, false).unwrap();
        write_out(b"BEFORE\n").unwrap();
        let staged = made.join("f");
        stage_write(staged.to_str().unwrap(), b"x".to_vec());
        std::fs::write(made.join("planted"), b"not ours").unwrap();

        assert!(!rewind(), "the plant makes the directory unremovable");
        assert_eq!(take_out(), b"BEFORE\n", "a failed undo threw away the output");
        commit().unwrap();
        assert_eq!(std::fs::read(&staged).unwrap(), b"x", "and the staged write with it");
        let _ = std::fs::remove_dir_all(&d);
    }

    /// …and what it already removed on the way to the failure goes back.
    ///
    /// Without it a staged write into the directory that DID come off is lost
    /// at the commit the `false` provokes. A run that has committed keeps its
    /// directories, which is the rule the rest of this module follows.
    #[test]
    fn a_failed_rewind_puts_back_what_it_had_already_taken() {
        let d = scratch("partial-undo");
        let a = d.join("a");
        let b = a.join("b");
        make_dir(a.to_str().unwrap(), false, false).unwrap();
        make_dir(b.to_str().unwrap(), false, false).unwrap();
        // In `a`, not in `b`: `b` comes off first and only `a` then fails.
        std::fs::write(a.join("planted"), b"not ours").unwrap();

        assert!(!rewind());
        assert!(b.is_dir(), "the directory that came off was not put back");
        assert!(a.is_dir());
        assert!(is_committed());
        let _ = std::fs::remove_dir_all(&d);
    }

    /// The line says `reached after {why}`, and `why` has three producers.
    #[test]
    fn the_run_names_the_thing_that_made_it_irreversible() {
        let d = scratch("why");
        assert_eq!(commit_reason(), "", "a fresh run has nothing to explain");
        let theirs = d.join("theirs");
        std::fs::create_dir(&theirs).unwrap();
        remove_dir(theirs.to_str().unwrap()).unwrap();
        assert_eq!(commit_reason(), WHY_FOREIGN_RMDIR);
        let _ = std::fs::remove_dir_all(&d);
    }

    #[test]
    fn a_stuck_directory_names_its_own_reason() {
        let d = scratch("why-stuck");
        let made = d.join("D");
        make_dir(made.to_str().unwrap(), false, false).unwrap();
        std::fs::write(made.join("planted"), b"not ours").unwrap();
        assert!(!rewind());
        assert_eq!(commit_reason(), WHY_DIR_STUCK);
        let _ = std::fs::remove_dir_all(&d);
    }

    /// `path_exists` says free, the disk says taken. Neither layer can serve
    /// it, so it refuses rather than raising the disk's `FileExistsError` over
    /// CPython's exit 0.
    #[test]
    fn mkdir_over_a_delete_this_run_has_not_committed_refuses() {
        let d = scratch("staged-delete");
        let f = d.join("F");
        std::fs::write(&f, b"here").unwrap();
        stage_delete(f.to_str().unwrap());
        let e = make_dir(f.to_str().unwrap(), false, false).unwrap_err();
        assert!(format!("{e}").contains("unsupported: mkdir"), "{e}");
        assert!(f.is_file(), "and the file the delete has not reached is untouched");
        discard();

        // The other order, and the worse one: the file was never on disk, so
        // `create_dir` would SUCCEED and the staged delete would then reach
        // `commit` to unlink a directory.
        let g = d.join("G");
        stage_write(g.to_str().unwrap(), b"x".to_vec());
        stage_delete(g.to_str().unwrap());
        let e = make_dir(g.to_str().unwrap(), false, false).unwrap_err();
        assert!(format!("{e}").contains("unsupported: mkdir"), "{e}");
        assert!(!g.exists(), "and nothing was made under a name a commit would unlink");
        discard();
        let _ = std::fs::remove_dir_all(&d);
    }

    /// A read this engine cannot finish is refused before it starts: every
    /// path here is read WHOLE, and a character device has no end.
    #[test]
    fn a_path_with_no_end_is_refused_before_it_is_read() {
        let d = scratch("regular");
        let f = d.join("f");
        std::fs::write(&f, b"abc").unwrap();
        require_regular_file(f.to_str().unwrap()).unwrap();
        require_regular_file(d.to_str().unwrap()).unwrap();
        require_regular_file(d.join("missing").to_str().unwrap()).unwrap();
        #[cfg(unix)]
        {
            let e = require_regular_file("/dev/zero").unwrap_err();
            assert!(format!("{e}").contains("open-special"), "{e}");
        }
        let _ = std::fs::remove_dir_all(&d);
    }

    #[test]
    fn mkdir_sees_a_staged_write_as_an_existing_path() {
        let d = scratch("staged");
        let f = d.join("F");
        stage_write(f.to_str().unwrap(), b"pending".to_vec());
        let e = make_dir(f.to_str().unwrap(), false, false).unwrap_err();
        assert!(format!("{e}").contains("FileExistsError"), "{e}");
        assert!(!f.exists(), "and no directory was made on top of the staged file");
        discard();
        let _ = std::fs::remove_dir_all(&d);
    }
}
