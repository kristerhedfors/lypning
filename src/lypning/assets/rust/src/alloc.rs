//! The allocator, because on this workload the allocator IS the workload.
//!
//! Callgrind on `t.split()` in a loop, 2026-08-24, static musl x86_64: **43.9%
//! of all instructions retired were inside musl's mallocng** — `alloc_slot`,
//! `nontrivial_free` and the two `meta.h` helpers. Not inside the parser, not
//! inside `eval`. mallocng is a hardened general-purpose allocator: it keeps
//! out-of-band metadata, validates it on every free, and randomises placement.
//! Every one of those properties is worth paying for in a long-lived server and
//! none of them is worth paying for here, where the process parses one
//! one-liner, allocates a few hundred thousand short-lived `Rc<str>`s and
//! exits.
//!
//! So this is a size-classed free-list allocator over bump-allocated chunks,
//! with the general allocator kept underneath for everything it is actually
//! good at. The shape is the oldest one there is and it is chosen for what it
//! does NOT do: no coalescing, no metadata validation, no per-object header.
//!
//!   * **Small and 16-aligned** (`<= MAX_SMALL`, `align <= GRAIN`) — a free
//!     list per 16-byte size class. A free block stores the next pointer in its
//!     own first word, so a live object carries no header at all and the class
//!     is recomputed from the `Layout` the caller must hand back. `alloc` is a
//!     pop, `dealloc` is a push.
//!   * **Everything else** — straight through to `System`. Big buffers are rare,
//!     want `mremap` on growth, and are exactly where a bump allocator's
//!     inability to give memory back would show.
//!
//! **What this trades away, stated plainly.** Chunks are never returned to the
//! OS and a size class never lends to another, so peak RSS is the high-water
//! mark of each class rather than of the program. That is the right trade for a
//! process whose whole life is milliseconds; it would be the wrong one for a
//! daemon, and `docs/LYPNING.md` §9 already rules a daemon out for other
//! reasons.
//!
//! **Why the lock, given the interpreter is single-threaded.** The crate also
//! builds as a `cdylib` (`capi.rs`), and a host that links it may call from two
//! threads even though a single run is sequential. `GlobalAlloc` is a `Sync`
//! trait and an allocator that is wrong under threads is wrong in a way that
//! shows up as memory corruption in someone else's application. An uncontended
//! `compare_exchange_weak` is a few cycles against mallocng's hundreds, so the
//! safe version is still overwhelmingly the fast one; there is no version of
//! this worth an `unsafe impl Sync` on a bare cell.

use std::alloc::{GlobalAlloc, Layout, System};
use std::cell::UnsafeCell;
use std::ptr;
use std::sync::atomic::{AtomicBool, Ordering};

/// Alignment every small block is guaranteed, and the granularity of a class.
/// 16 is `max_align_t` on this target, so a request asking for more is rare
/// enough to be worth nothing and is handed to `System` instead.
const GRAIN: usize = 16;

/// 16 classes of 16 bytes. The distribution this is sized for, measured from
/// the value model rather than guessed: an `Rc<str>` of a short string is 16
/// bytes of header plus its length, a `Value` is 40, a `Rc<RefCell<Vec<Value>>>`
/// is 48, a small `Vec<Value>` is 40 per element. Everything the interpreter
/// allocates in a loop lands under 256 bytes; the tail above it is buffers,
/// which belong to `System` anyway.
const CLASSES: usize = 16;
const MAX_SMALL: usize = CLASSES * GRAIN;

/// Carved from `System` and never given back. 64 KiB is 4,096 of the smallest
/// class — large enough that the carve is not itself a hot path, small enough
/// that a program allocating nothing pays for one page of touched memory and
/// not a megabyte of it. Nothing is touched until it is handed out: the bump
/// pointer walks forward through untouched pages the kernel has not yet backed.
const CHUNK: usize = 64 * 1024;

struct Pool {
    /// Head of each class's free list, or null. The next pointer of a free
    /// block lives in the block's own first word.
    free: [*mut u8; CLASSES],
    /// The unused tail of the current chunk.
    bump: *mut u8,
    end: *mut u8,
}

impl Pool {
    const fn new() -> Self {
        Pool { free: [ptr::null_mut(); CLASSES], bump: ptr::null_mut(), end: ptr::null_mut() }
    }
}

pub struct Lypalloc {
    /// Uncontended in the binary; see the module docstring for why it exists.
    lock: AtomicBool,
    pool: UnsafeCell<Pool>,
}

// The `UnsafeCell` is only ever reached while `lock` is held, which is what
// makes this sound; the marker cannot be derived and has to be asserted.
unsafe impl Sync for Lypalloc {}

impl Lypalloc {
    pub const fn new() -> Self {
        Lypalloc { lock: AtomicBool::new(false), pool: UnsafeCell::new(Pool::new()) }
    }

    #[inline(always)]
    fn acquire(&self) {
        while self
            .lock
            .compare_exchange_weak(false, true, Ordering::Acquire, Ordering::Relaxed)
            .is_err()
        {
            std::hint::spin_loop();
        }
    }

    #[inline(always)]
    fn release(&self) {
        self.lock.store(false, Ordering::Release);
    }
}

/// The class index for a layout, or `None` if it belongs to `System`.
///
/// Deliberately a pure function of the `Layout`: `dealloc` is handed the same
/// layout `alloc` was given, so the class does not have to be stored anywhere
/// and a live block carries no header. A zero-sized request cannot reach here —
/// `GlobalAlloc` forbids it — but `size == 0` would map to class 0 harmlessly
/// anyway.
#[inline(always)]
fn class_of(layout: Layout) -> Option<usize> {
    if layout.align() > GRAIN || layout.size() > MAX_SMALL {
        return None;
    }
    // `(size + 15) / 16 - 1`, with size 0 clamped to class 0.
    Some((layout.size().wrapping_add(GRAIN - 1) / GRAIN).saturating_sub(1))
}

unsafe impl GlobalAlloc for Lypalloc {
    /// `inline(never)`, and it is not a detail. With `#[inline]` here the three
    /// methods are duplicated into the `__rust_alloc` shims and everything that
    /// reaches them, and the binary came out **8,192 bytes larger** for no
    /// measurable speed — the fast path is a pop and a store, so the call is
    /// already most of the remaining cost and duplicating it buys nothing. The
    /// image is a step function in 131,072 B device blocks (`docs/LYPNING.md`
    /// §8), so those bytes are not free the way they would be anywhere else.
    #[inline(never)]
    unsafe fn alloc(&self, layout: Layout) -> *mut u8 {
        let class = match class_of(layout) {
            Some(c) => c,
            None => return System.alloc(layout),
        };
        let size = (class + 1) * GRAIN;
        self.acquire();
        let pool = &mut *self.pool.get();
        let head = pool.free[class];
        if !head.is_null() {
            // Pop. The next pointer was written into the block by `dealloc`.
            pool.free[class] = *(head as *mut *mut u8);
            self.release();
            return head;
        }
        // Carve from the current chunk, taking a new one when it is exhausted.
        // The remainder of the old chunk is abandoned rather than threaded onto
        // a free list: it is under 256 bytes, once per 64 KiB, and the code to
        // reclaim it would cost more bytes in the image than the memory is
        // worth (`docs/LYPNING.md` §8 — the image is a step function in
        // 131,072 B device blocks).
        if (pool.end as usize) - (pool.bump as usize) < size {
            // `System` is called with the lock held. Sound because musl's
            // allocator does not call back into the global allocator, so there
            // is no path from here to `acquire` again.
            let chunk = System.alloc(Layout::from_size_align_unchecked(CHUNK, GRAIN));
            if chunk.is_null() {
                self.release();
                return ptr::null_mut();
            }
            pool.bump = chunk;
            pool.end = chunk.add(CHUNK);
        }
        let out = pool.bump;
        pool.bump = out.add(size);
        self.release();
        out
    }

    #[inline(never)]
    unsafe fn dealloc(&self, p: *mut u8, layout: Layout) {
        let class = match class_of(layout) {
            Some(c) => c,
            None => return System.dealloc(p, layout),
        };
        self.acquire();
        let pool = &mut *self.pool.get();
        *(p as *mut *mut u8) = pool.free[class];
        pool.free[class] = p;
        self.release();
    }

    /// Overridden only for the case the default would get badly wrong: a large
    /// block growing. `GlobalAlloc`'s default is alloc-copy-dealloc, which for a
    /// buffer being doubled in a loop turns `mremap` into a full copy every
    /// time — and a growing buffer is exactly the shape of `s += x`, already the
    /// worst row in `lypning perf`. Small blocks keep the default path, where
    /// the copy is at most 256 bytes and the pop is cheaper than anything
    /// `System` could do.
    #[inline(never)]
    unsafe fn realloc(&self, p: *mut u8, layout: Layout, new_size: usize) -> *mut u8 {
        if class_of(layout).is_none()
            && layout.align() <= GRAIN
            && new_size > MAX_SMALL
        {
            return System.realloc(p, layout, new_size);
        }
        // Default: allocate under the new layout, copy the overlap, free.
        let new_layout = Layout::from_size_align_unchecked(new_size, layout.align());
        let out = self.alloc(new_layout);
        if !out.is_null() {
            ptr::copy_nonoverlapping(p, out, layout.size().min(new_size));
            self.dealloc(p, layout);
        }
        out
    }
}
