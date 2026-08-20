/*
 * lypning-mp — slicing with a step, for str, bytes, list and tuple.
 *
 * `s[::-1]` is one of the most common idioms in the language and MicroPython
 * does not implement it: `mp_seq_get_fast_slice_indexes()` returns false for
 * any step other than 1, and every container then raises
 * NotImplementedError("only slices with step=1 (aka None) are supported").
 * docs/SUBSET.md §3.1 puts "slicing with an optional step including
 * negative" in Tier 0 on the strength of the corpus entry `str-slicing`.
 *
 * There is no build option for it. What upstream DOES already have is
 * mp_obj_slice_indices(), which resolves start/stop/step correctly for any
 * step including negative ones — it is what `slice.indices()` is built on. So
 * the missing piece is only the walk, and that is what this header adds.
 *
 * It lives beside the variant rather than in the port patch so the patch stays
 * four one-line call sites instead of four blocks of logic, which is what keeps
 * a MicroPython version bump a rebase.
 *
 * MIT, same as MicroPython.
 */

#ifndef LYPNING_SLICE_H
#define LYPNING_SLICE_H

#include "py/obj.h"
#include "py/objstr.h"
#include "py/runtime.h"

// Number of elements a slice yields. Both directions, and never negative.
static inline size_t lypning_slice_count(const mp_bound_slice_t *b) {
    if (b->step > 0) {
        return b->start >= b->stop ? 0 : (size_t)((b->stop - b->start + b->step - 1) / b->step);
    }
    return b->start <= b->stop ? 0 : (size_t)((b->start - b->stop - b->step - 1) / -b->step);
}

/* list and tuple: the elements are already an array, so this is just the walk. */
static inline mp_obj_t lypning_slice_seq(const mp_obj_t *items, size_t len, mp_obj_t slice_in, bool as_tuple) {
    mp_bound_slice_t b;
    mp_obj_slice_indices(slice_in, (mp_int_t)len, &b);
    size_t n = lypning_slice_count(&b);
    mp_obj_t *out = m_new(mp_obj_t, n == 0 ? 1 : n);
    mp_int_t i = b.start;
    for (size_t k = 0; k < n; k++, i += b.step) {
        out[k] = items[i];
    }
    mp_obj_t res = as_tuple ? mp_obj_new_tuple(n, out) : mp_obj_new_list(n, out);
    if (as_tuple) {
        m_del(mp_obj_t, out, n == 0 ? 1 : n);
    }
    return res;
}

/*
 * str and bytes.
 *
 * Two paths, because with MICROPY_PY_BUILTINS_STR_UNICODE a character index is
 * not a byte offset. When the string is pure ASCII — which is every corpus
 * entry that slices, and almost every line an agent pipes through — the two
 * coincide and the walk is O(output). Otherwise each character index is
 * resolved through str_index_to_ptr(), which scans from one end, making the
 * non-ASCII path O(n·k). That is bounded and rare; the alternative is building
 * an offset table proportional to the string, which is the wrong trade in a VM
 * whose memory is the browser's.
 *
 * `is_str` distinguishes the two callers: bytes yields bytes, str yields str.
 */
static inline mp_obj_t lypning_slice_str(const mp_obj_type_t *type, const byte *data,
    size_t len_bytes, mp_obj_t slice_in, bool is_unicode) {
    size_t nchars = is_unicode ? utf8_charlen(data, len_bytes) : len_bytes;
    mp_bound_slice_t b;
    mp_obj_slice_indices(slice_in, (mp_int_t)nchars, &b);
    size_t n = lypning_slice_count(&b);
    if (n == 0) {
        return mp_obj_new_str_of_type(type, data, 0);
    }

    vstr_t vstr;
    vstr_init(&vstr, n);
    if (!is_unicode || nchars == len_bytes) {
        // ASCII (or a bytes object): index == offset.
        mp_int_t i = b.start;
        for (size_t k = 0; k < n; k++, i += b.step) {
            vstr_add_byte(&vstr, data[i]);
        }
    } else {
        mp_int_t i = b.start;
        for (size_t k = 0; k < n; k++, i += b.step) {
            const byte *p = str_index_to_ptr(type, data, len_bytes, MP_OBJ_NEW_SMALL_INT(i), false);
            const byte *q = utf8_next_char(p);
            vstr_add_strn(&vstr, (const char *)p, q - p);
        }
    }
    return mp_obj_new_str_from_vstr(&vstr);
}

#endif // LYPNING_SLICE_H
