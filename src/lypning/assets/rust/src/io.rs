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
//!   * exit 90 discards all of it, so the program is observably a no-op.
//!
//! The one escape hatch is size: past `COMMIT_THRESHOLD` bytes of output the
//! buffer is flushed early and the run becomes COMMITTED. A later `unsupported`
//! then cannot be retried, so it is reported as a hard error instead of a
//! routing signal. In the corpus that threshold has never been reached — the
//! whole population is one-liners — but silently corrupting a large stream
//! would be the worst possible failure, so the case is handled rather than
//! assumed away.

use crate::err::{unsupported, LypningError, R};
use crate::host;
use std::cell::RefCell;
use crate::hash::Map;
use std::io::{Read, Write};
#[cfg(feature = "cap-csv")]
use std::rc::Rc;

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
    /// Read in two places, and it took a differential grid to find the second.
    /// `csv.rs`'s `split_lines` is the obvious one — to a `csv.reader` the mode
    /// is the difference between one record and two. `iter::line_end` is the
    /// one the first cut missed: `newline=''` means a line ends at `\r\n`,
    /// `\n` OR a bare `\r`, so a file object that was SERVED under that flag
    /// while still splitting at `\n` alone swallowed every bare CR, and
    /// `readline`, `readlines`, `for line in f` and `seek(0)` all went with it.
    /// Serving a flag and then not reading it is worse than refusing it.
    ///
    /// The core neither serves nor stores the mode: `newline=''` refuses there,
    /// exactly as it did before `cap-csv` existed.
    #[cfg(feature = "cap-csv")]
    pub newline_mode: u8,
    /// Set when a `csv.reader` took the rest of this stream in one gulp.
    ///
    /// CPython's reader is LAZY: after `next(r)` the file is positioned just
    /// past the first record and `f.read()` returns the rest. This one is
    /// eager, so the position it leaves behind is right only when the reader
    /// was drained — which every corpus shape does and a `next(r)` followed by
    /// `f.read()` does not. Rather than answer that difference wrongly at exit
    /// 0, the stream remembers and every later READ of it refuses; CPython
    /// answers one spawn later. Found by a 2,773-row differential grid, in the
    /// two rows that did it — and left open for `sys.stdin`, which has its own
    /// flag ([`stdin_csv_guard`]) because it is not a `FileObj`.
    #[cfg(feature = "cap-csv")]
    pub csv_consumed: bool,
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
    static DELETED: RefCell<crate::hash::Set<String>> =
        RefCell::new(crate::hash::Set::with_hasher(crate::hash::BuildFnv));
}

/// Record that something irreversible happened outside the staging area.
///
/// The barrier stages file WRITES and deletes, but a directory is created and
/// removed immediately — there is nothing to stage, since `os.mkdir` has no
/// content to hold back. So the run stops being reversible at that moment and
/// must say so: without this, a program that made a directory and then hit an
/// unsupported construct reported `committed = false`, the caller re-ran it on
/// CPython, and the second `os.mkdir` raised `FileExistsError` for a program
/// that works.
pub fn mark_committed() {
    COMMITTED.with(|c| *c.borrow_mut() = true);
}

pub fn is_committed() -> bool {
    COMMITTED.with(|c| *c.borrow())
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
            COMMITTED.with(|c| *c.borrow_mut() = true);
        }
        return Ok(());
    }
    if !is_committed() && buffered_len() > COMMIT_THRESHOLD {
        commit()?;
        COMMITTED.with(|c| *c.borrow_mut() = true);
    }
    Ok(())
}

/// Flush everything staged. Called once, at a successful exit.
pub fn commit() -> R<()> {
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
                let mut base = std::fs::read(path).unwrap_or_default();
                base.extend_from_slice(&buf);
                Ok(Some(base))
            } else {
                Ok(Some(buf))
            }
        }
    }
}

/// Paths this run has deleted or renamed away but not yet committed.
pub fn is_staged_deleted(path: &str) -> bool {
    DELETED.with(|d| d.borrow().contains(path))
}

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
                std::fs::read(path).map_err(|e| os_error(path, &e))?
            }
        }
    } else {
        // `open(p,'w')` TRUNCATES in CPython, and `open(p,'a')` moves the end a
        // lazy reader has not reached yet; either is a write a live
        // `csv.reader` over the same path would see and this engine's eager one
        // cannot.
        csv_on_write(path)?;
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
        csv_consumed: false,
    })
}

/// The read half of `FileObj::csv_consumed`: every path that would observe the
/// position a `csv.reader` left behind, and nothing else. `close` and `write`
/// are not among them — neither can see it.
#[cfg(feature = "cap-csv")]
pub fn csv_read_guard(f: &FileObj) -> R<()> {
    if f.csv_consumed {
        return Err(unsupported(
            "csv",
            "reading a file that csv.reader() has already taken (CPython's reader is lazy and leaves the stream where the last row it yielded ended)",
        ));
    }
    Ok(())
}

#[cfg(not(feature = "cap-csv"))]
pub fn csv_read_guard(_f: &FileObj) -> R<()> {
    Ok(())
}

// The `sys.stdin` half of [`csv_read_guard`], and the one stream that has no
// other half: a file can be reopened and read again, stdin cannot.
//
// `csv.reader(sys.stdin)` reaches [`stdin_rest`], which DRAINS — and the first
// cut of this capability recorded that on `FileObj` and nowhere else, so every
// later `sys.stdin.read()`, `.readline()`, `.readlines()` and `for line in
// sys.stdin` answered empty at exit 0 where CPython, whose reader is lazy,
// still has the whole stream. One match arm away from the guard that was
// already right.
#[cfg(feature = "cap-csv")]
thread_local! {
    static STDIN_CSV: RefCell<bool> = const { RefCell::new(false) };
}

#[cfg(feature = "cap-csv")]
pub fn stdin_csv_take() {
    STDIN_CSV.with(|s| *s.borrow_mut() = true);
}

#[cfg(feature = "cap-csv")]
pub fn stdin_csv_guard() -> R<()> {
    if STDIN_CSV.with(|s| *s.borrow()) {
        return Err(unsupported(
            "csv",
            "reading sys.stdin after csv.reader() has taken it (CPython's reader is lazy and leaves the stream where the last row it yielded ended)",
        ));
    }
    Ok(())
}

#[cfg(not(feature = "cap-csv"))]
pub fn stdin_csv_guard() -> R<()> {
    Ok(())
}

/// Why a registered reader was killed: its file was closed, so the next row is
/// CPython's `ValueError: I/O operation on closed file.`
#[cfg(feature = "cap-csv")]
pub const CSV_DEAD_CLOSED: u8 = 0;
/// … or its file was written to after it had been drained, so the next row is a
/// row CPython would still find and this engine no longer can.
#[cfg(feature = "cap-csv")]
pub const CSV_DEAD_STALE: u8 = 1;

// Every live `csv.reader` and the file it was taken from.
//
// The eager reader is right about the ROWS and wrong about the MOMENT, and the
// two moments a program can see the difference are both events on the FILE:
// closing it (CPython's lazy reader then raises on the next row) and writing to
// it (CPython's lazy reader then sees what was written). Neither is visible
// from the reader, so the file tells the reader instead — which is why this
// list exists rather than a flag on either side.
//
// An entry whose reader has a strong count of 1 is held by nothing but this
// list: the program has dropped it, no `next` can ever reach it again, and it
// is pruned rather than answered for. That is what keeps the common shape —
// `rows = list(csv.reader(open(p)))` and then `open(p,'w').write(…)` — from
// paying a refusal for a reader nobody can look at.
#[cfg(feature = "cap-csv")]
thread_local! {
    static CSV_READERS: RefCell<Vec<CsvReader>> = const { RefCell::new(Vec::new()) };
}

#[cfg(feature = "cap-csv")]
struct CsvReader {
    file: Rc<RefCell<FileObj>>,
    /// Copied at registration so a scan never has to borrow the `FileObj` —
    /// `file_write` is already holding one.
    path: String,
    rows: Rc<RefCell<crate::iter::Iter>>,
}

#[cfg(feature = "cap-csv")]
pub fn csv_register(file: &Rc<RefCell<FileObj>>, rows: &Rc<RefCell<crate::iter::Iter>>) {
    let path = file.borrow().path.clone();
    CSV_READERS.with(|r| {
        let mut r = r.borrow_mut();
        // Prune here as well as on close and write, so a loop over a directory
        // of CSVs keeps at most its LIVE readers: an entry holds the file's
        // whole buffer, and the list is the only thing keeping a dropped
        // reader's copy of it alive.
        r.retain(|e| Rc::strong_count(&e.rows) > 1);
        r.push(CsvReader { file: file.clone(), path, rows: rows.clone() })
    });
}

/// `f.close()`. CPython's reader holds the FILE, so the next row after a close
/// is `ValueError: I/O operation on closed file.` — for a drained reader too,
/// which is why this fires on every reader over the file and not only on the
/// ones with rows left. The drained-and-never-touched-again shape (`with open(p)
/// as f: rows = list(csv.reader(f))`) is unaffected: nothing asks for another
/// row, so nothing raises.
#[cfg(feature = "cap-csv")]
pub fn csv_on_close(file: &Rc<RefCell<FileObj>>) {
    CSV_READERS.with(|r| {
        let mut r = r.borrow_mut();
        for e in r.iter() {
            if Rc::ptr_eq(&e.file, file) && Rc::strong_count(&e.rows) > 1 {
                if let Ok(mut b) = e.rows.try_borrow_mut() {
                    *b = crate::iter::Iter::CsvDead(CSV_DEAD_CLOSED);
                }
            }
        }
        r.retain(|e| Rc::strong_count(&e.rows) > 1);
    });
}

#[cfg(not(feature = "cap-csv"))]
pub fn csv_on_close(_file: &std::rc::Rc<RefCell<FileObj>>) {}

/// A write to `path`. CPython's reader would see it; this one read the file
/// once, at construction, so it cannot. A reader with rows left REFUSES (exit
/// 90, and CPython answers one spawn later); a drained one is marked stale, so
/// the write itself is allowed and only a further `next` on that reader
/// refuses. Both directions were reachable at exit 0 before.
#[cfg(feature = "cap-csv")]
pub fn csv_on_write(path: &str) -> R<()> {
    CSV_READERS.with(|r| {
        let mut r = r.borrow_mut();
        let mut stale: Vec<Rc<RefCell<crate::iter::Iter>>> = Vec::new();
        for e in r.iter() {
            if e.path != path || Rc::strong_count(&e.rows) == 1 {
                continue;
            }
            match e.rows.try_borrow() {
                Ok(b) if crate::iter::drained(&b) => stale.push(e.rows.clone()),
                _ => {
                    return Err(unsupported(
                        "csv",
                        "writing a file a csv.reader() still has rows to yield (CPython's reader is lazy and would yield what is written)",
                    ))
                }
            }
        }
        for it in stale {
            if let Ok(mut b) = it.try_borrow_mut() {
                *b = crate::iter::Iter::CsvDead(CSV_DEAD_STALE);
            }
        }
        r.retain(|e| Rc::strong_count(&e.rows) > 1);
        Ok(())
    })
}

#[cfg(not(feature = "cap-csv"))]
pub fn csv_on_write(_path: &str) -> R<()> {
    Ok(())
}

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
    csv_on_write(&f.path)?;
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
    COMMITTED.with(|c| *c.borrow_mut() = false);
    STDIN.with(|s| *s.borrow_mut() = None);
    STDIN_POS.with(|p| *p.borrow_mut() = 0);
}
