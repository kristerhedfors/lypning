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
const SMALL_CLASSES: usize = 16;
const MAX_SMALL: usize = SMALL_CLASSES * GRAIN;

/// Above 256 B the classes go by powers of two, 512 B through 128 KiB, and the
/// reason is a syscall count rather than an instruction count.
///
/// musl hands a medium allocation straight to `mmap` and gives it back with
/// `munmap`. `s += 'x'` twenty thousand times allocates every length from 1 to
/// 20,000 and frees the previous one — measured on this container, **16,055
/// `mmap` and 16,047 `munmap`**, 32,104 syscalls for one one-liner, and
/// `str-concat` has been the worst row in `lypning perf` for as long as the
/// table has existed. With power-of-two classes the whole run of lengths from
/// 2,049 to 4,096 is one 4 KiB block handed back and forth, so the syscalls
/// collapse to one per class transition: **nine, not sixteen thousand.**
///
/// The trade is rounding waste, and it is bounded at **2x** — `alloc(513)` gets
/// 1,024 B. That is the right trade for a process whose life is milliseconds and
/// the wrong one for anything long-lived; measured peak RSS is in the ledger
/// beside the syscall count, because "bounded at 2x" is a proof and not a
/// measurement.
///
/// The ceiling matters as much as the floor. Above 128 KiB an allocation goes to
/// `System` untouched, so a genuinely large buffer keeps `mremap` on growth —
/// a `Vec` doubling toward 8 MiB wants the kernel to move a page table, not to
/// copy 8 MiB into a block we cached.
const LARGE_MIN_SHIFT: u32 = 9; // 512 B
const LARGE_MAX_SHIFT: u32 = 17; // 128 KiB
const LARGE_CLASSES: usize = (LARGE_MAX_SHIFT - LARGE_MIN_SHIFT + 1) as usize;
const MAX_LARGE: usize = 1 << LARGE_MAX_SHIFT;

const CLASSES: usize = SMALL_CLASSES + LARGE_CLASSES;

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
fn class_of(layout: Layout) -> Option<(usize, usize)> {
    if layout.align() > GRAIN {
        return None;
    }
    let size = layout.size();
    if size <= MAX_SMALL {
        // `(size + 15) / 16 - 1`, with size 0 clamped to class 0.
        let c = (size.wrapping_add(GRAIN - 1) / GRAIN).saturating_sub(1);
        return Some((c, (c + 1) * GRAIN));
    }
    if size > MAX_LARGE {
        return None;
    }
    // Round up to a power of two, floored at 512 B: `ceil(log2(size))`.
    let shift = (usize::BITS - (size - 1).leading_zeros()).max(LARGE_MIN_SHIFT);
    Some((
        SMALL_CLASSES + (shift - LARGE_MIN_SHIFT) as usize,
        1usize << shift,
    ))
}

unsafe impl GlobalAlloc for Lypalloc {
    /// `#[inline]`, and it was `inline(never)` for four iterations because the
    /// bytes could not be afforded — the image is a step function in 131,072 B
    /// device blocks (`docs/LYPNING.md` §8) and there were 3,400 bytes of
    /// headroom when this file was written. Boxing the error payload (ledger,
    /// iteration 22) freed 45,056 B, and the attribute was re-measured rather
    /// than assumed.
    ///
    /// It costs **4,096 B** and is worth it. The reading is a hard one to take
    /// honestly, because it is the size where this profile's own inlining
    /// decisions move on their own, so it was taken the way the skill says to:
    /// **four builds of the unchanged source** with a comment moved in an
    /// unrelated file, against **three of the changed** one. Perf TOTAL, ms:
    ///
    ///   unchanged  1690.46  1703.90  1705.45  1726.75
    ///   `#[inline]` 1634.44  1653.03  1662.16
    ///
    /// The bands do not overlap — the worst inlined build beats the best
    /// non-inlined one by 28 ms — so the ~3.3% is a real difference and not the
    /// linker. `membership`, `dict-set` and `str-repr` carry most of it.
    #[inline]
    unsafe fn alloc(&self, layout: Layout) -> *mut u8 {
        let (class, size) = match class_of(layout) {
            Some(c) => c,
            None => return System.alloc(layout),
        };
        self.acquire();
        let pool = &mut *self.pool.get();
        let head = pool.free[class];
        if !head.is_null() {
            // Pop. The next pointer was written into the block by `dealloc`.
            pool.free[class] = *(head as *mut *mut u8);
            self.release();
            return head;
        }
        // A large class is taken from `System` at its own size rather than
        // carved: one of them can be half the chunk. It is cached on free like
        // any other class, which is the entire point — the syscall happens once
        // per class rather than once per allocation.
        if size > MAX_SMALL {
            self.release();
            return System.alloc(Layout::from_size_align_unchecked(size, GRAIN));
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

    #[inline]
    unsafe fn dealloc(&self, p: *mut u8, layout: Layout) {
        let (class, _) = match class_of(layout) {
            Some(c) => c,
            None => return System.dealloc(p, layout),
        };
        self.acquire();
        let pool = &mut *self.pool.get();
        *(p as *mut *mut u8) = pool.free[class];
        pool.free[class] = p;
        self.release();
    }

    /// Three cases, and only the middle one is `GlobalAlloc`'s default.
    ///
    /// **Same class in, same class out — return the pointer.** A block is
    /// already its whole class, so growing from 2,049 to 4,096 bytes needs no
    /// allocation and no copy at all. `dealloc` will later be handed the *new*
    /// layout, which maps to the same class, so the block goes back on the right
    /// free list; that is what makes returning `p` sound rather than merely
    /// convenient.
    ///
    /// **Above the cached range at both ends — hand it to `System`.** A `Vec`
    /// doubling toward 8 MiB wants `mremap` to move a page table, not a copy of
    /// 8 MiB into a block we cached. Losing that is how a bump allocator makes a
    /// program slower rather than faster.
    ///
    /// **Otherwise the default**, alloc-copy-dealloc through this allocator.
    #[inline]
    unsafe fn realloc(&self, p: *mut u8, layout: Layout, new_size: usize) -> *mut u8 {
        let old = class_of(layout);
        let new = class_of(Layout::from_size_align_unchecked(new_size, layout.align()));
        if let (Some((a, _)), Some((b, _))) = (old, new) {
            if a == b {
                return p;
            }
        }
        if old.is_none() && new.is_none() && layout.align() <= GRAIN {
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
