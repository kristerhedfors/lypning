//! Files, streams, and the **commit barrier**.
//!
//! The barrier is what makes a mixture-of-interpreters safe. Routing a program
//! to lypning is only sound if a lypning run that ends in `unsupported` left nothing
//! behind — otherwise the retry on lypning-mp or CPython re-executes the side
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
}

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
    })
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
