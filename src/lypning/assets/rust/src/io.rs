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
use std::cell::RefCell;
use std::collections::HashMap;
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
    pub files: HashMap<String, (Vec<u8>, bool)>,
    /// Ordered so the flush reproduces the program's own write order.
    pub order: Vec<String>,
}

thread_local! {
    static OUT: RefCell<Vec<u8>> = const { RefCell::new(Vec::new()) };
    static ERRBUF: RefCell<Vec<u8>> = const { RefCell::new(Vec::new()) };
    static PENDING: RefCell<Pending> = RefCell::new(Pending::default());
    static COMMITTED: RefCell<bool> = const { RefCell::new(false) };
    static DELETED: RefCell<std::collections::HashSet<String>> =
        RefCell::new(std::collections::HashSet::new());
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

fn buffered_len() -> usize {
    OUT.with(|o| o.borrow().len())
        + ERRBUF.with(|o| o.borrow().len())
        + PENDING.with(|p| p.borrow().files.values().map(|(b, _)| b.len()).sum::<usize>())
}

fn maybe_commit() -> R<()> {
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
    STDIN.with(|s| {
        let mut s = s.borrow_mut();
        if s.is_none() {
            let mut buf = Vec::new();
            std::io::stdin()
                .read_to_end(&mut buf)
                .map_err(|e| os_error("<stdin>", &e))?;
            *s = Some(buf);
        }
        Ok(s.as_ref().unwrap().clone())
    })
}

pub fn stdin_consumed() -> Option<Vec<u8>> {
    STDIN.with(|s| s.borrow().clone())
}

pub fn stdin_rest() -> R<Vec<u8>> {
    let all = stdin_all()?;
    let pos = STDIN_POS.with(|p| *p.borrow());
    STDIN_POS.with(|p| *p.borrow_mut() = all.len());
    Ok(all[pos.min(all.len())..].to_vec())
}

pub fn stdin_line() -> R<Option<Vec<u8>>> {
    let all = stdin_all()?;
    let pos = STDIN_POS.with(|p| *p.borrow());
    if pos >= all.len() {
        return Ok(None);
    }
    let end = match all[pos..].iter().position(|c| *c == b'\n') {
        Some(i) => pos + i + 1,
        None => all.len(),
    };
    STDIN_POS.with(|p| *p.borrow_mut() = end);
    Ok(Some(all[pos..end].to_vec()))
}
