"""The gate's arithmetic. No wall clock, no strace, no binary.

Everything measured here is measured elsewhere by running something, which is
exactly what does not belong in CI. What belongs is the two pure functions the
numbers are fed through: the device-block step function that makes one byte cost
131,071 more, and the cold-cost projection — including its refusal to produce a
number when the baseline was never taken, since a plausible-looking estimate
built on nothing is worse than no estimate.
"""

from __future__ import annotations

import pytest

from lypning import gate


@pytest.mark.parametrize("size,blocks", [
    (0, 0),
    (-1, 0),
    (1, 1),
    (gate.DEVICE_BLOCK, 1),
    (gate.DEVICE_BLOCK + 1, 2),
    (541_688, 5),        # the MicroPython prototype
    (5 * gate.DEVICE_BLOCK, 5),
    (5 * gate.DEVICE_BLOCK + 1, 6),  # the byte that costs a whole block
])
def test_device_blocks_rounds_up(size, blocks):
    assert gate.device_blocks(size) == blocks


MEASURED_BASELINE = {"measured": True, "exists": True, "device_blocks": 8,
                     "opens": 22, "cold_ms": 8000}


def test_cold_cost_scales_on_bytes_when_bytes_dominate():
    assert gate.project_cold_ms(2 * gate.DEVICE_BLOCK, 0, MEASURED_BASELINE) == 2000


def test_cold_cost_scales_on_opens_when_opens_dominate():
    # The pessimistic read: cold cost is bytes fetched AND round trips taken,
    # and neither term alone explains it, so the larger share wins.
    assert gate.project_cold_ms(gate.DEVICE_BLOCK, 11, MEASURED_BASELINE) == 4000


@pytest.mark.parametrize("opens,baseline", [
    (None, MEASURED_BASELINE),
    (0, dict(MEASURED_BASELINE, measured=False)),
    (0, dict(MEASURED_BASELINE, exists=False)),
])
def test_cold_cost_is_none_without_a_measurement(opens, baseline):
    assert gate.project_cold_ms(700_000, opens, baseline) is None


def test_size_of_a_binary_that_is_not_there_is_zero(tmp_path):
    # A missing artifact is caught by its own check, not by an exception here.
    assert gate.size_bytes(tmp_path / "absent") == 0


def test_the_rust_core_is_measured_against_its_own_budget():
    # Two runtimes with different jobs: the lypning-mp byte budget is not a
    # verdict on the Rust core, and reporting it as one would invent a number
    # no document argues for.
    over = gate.MAX_BYTES * 3
    # A Rust variant is gated in device blocks against its own budget — this
    # used to pass anything for `lypning`, and a spectrum whose premise is
    # bytes-per-point cannot leave its points ungated. 2.1 MB is 17 blocks.
    assert not gate._size_check("lypning", over).ok
    assert gate._size_check("lypning", 8 * gate.DEVICE_BLOCK).ok
    assert not gate._size_check("lypning", 8 * gate.DEVICE_BLOCK + 1).ok
    assert gate._size_check("lypning", 8 * gate.DEVICE_BLOCK).unit == "blocks"
    assert gate._size_check("lypning-l", over).ok            # 17 blocks fits the 32-block ceiling
    assert not gate._size_check("lypning-l", 33 * gate.DEVICE_BLOCK).ok
    assert not gate._size_check("lypning-mp", over).ok
    assert gate._size_check("lypning-mp", gate.MAX_BYTES).ok


# --- the code section, beside the file size ----------------------------------
#
# The debt this pays: `build --rust` printed FILE bytes, and on Darwin arm64 the
# Mach-O `__TEXT` segment is padded to 16,384 B — so one commit added 2,384 B of
# `__text` to the frozen core and its file size did not move at all, while
# lypning-l's file grew 6.5x more than its code did. One capability, measured
# from three denominators, read 0.99, 0.66 and 0.89 programs per KB.


def _elf(text_size: int, *, wide: bool = True, little: bool = True,
         name: bytes = b".text") -> bytes:
    """A minimal ELF: a null section, one named section, and the string table.

    Written by hand rather than compiled, because the point of reading the
    section headers in Python is that no toolchain has to be present — a test
    that shelled out to `readelf` would skip on exactly the machines the reader
    exists for.
    """
    import struct
    end = "<" if little else ">"
    names = b"\x00" + name + b"\x00.shstrtab\x00"
    ehsize, shentsize, word = (64, 64, "Q") if wide else (52, 40, "I")
    shoff = ehsize + len(names)
    hdr = bytearray(ehsize)
    hdr[0:4], hdr[4], hdr[5] = b"\x7fELF", 2 if wide else 1, 1 if little else 2
    at = (0x28, 0x3A, 0x3C, 0x3E) if wide else (0x20, 0x2E, 0x30, 0x32)
    struct.pack_into(end + word, hdr, at[0], shoff)
    for offset, value in zip(at[1:], (shentsize, 3, 2)):
        struct.pack_into(end + "H", hdr, offset, value)

    def section(name_off: int, off: int, size: int) -> bytes:
        fields = (name_off, 1, 0, 0, off, size, 0, 0, 1, 0)
        return struct.pack(end + ("IIQQQQIIQQ" if wide else "IIIIIIIIII"), *fields)

    return (bytes(hdr) + names + section(0, 0, 0)
            + section(1, 0, text_size) + section(len(name) + 2, ehsize, len(names)))


@pytest.mark.parametrize("wide", [True, False])
@pytest.mark.parametrize("little", [True, False])
def test_elf_code_size_is_read_out_of_the_section_headers(tmp_path, wide, little):
    # All four ELF shapes, because the shipping target is a musl ELF cross-built
    # from whichever host is to hand, and the reader is the same on every one.
    p = tmp_path / "a.elf"
    p.write_bytes(_elf(4242, wide=wide, little=little))
    size, note = gate.text_bytes(p)
    assert size == 4242
    assert not note.startswith(gate.UNMEASURED)
    # And it is NOT the file size — which is the whole point of the column.
    assert size != p.stat().st_size


def test_an_elf_without_a_text_section_is_a_hole_not_a_zero(tmp_path):
    p = tmp_path / "b.elf"
    p.write_bytes(_elf(4242, name=b".data"))
    size, note = gate.text_bytes(p)
    assert size is None
    assert note.startswith(gate.UNMEASURED)


MACHO64 = b"\xcf\xfa\xed\xfe"


def test_macho_code_size_comes_from_size_m(tmp_path, monkeypatch):
    import subprocess as sp
    p = tmp_path / "c.macho"
    p.write_bytes(MACHO64 + b"\x00" * 64)
    monkeypatch.setattr(gate.shutil, "which", lambda name: "/usr/bin/size")
    monkeypatch.setattr(gate, "_run", lambda *a, **k: sp.CompletedProcess(
        a[0], 0, "Segment __TEXT: 770048\n\tSection __text: 657700\n\ttotal 764030\n", ""))
    size, note = gate.text_bytes(p)
    assert size == 657700
    assert not note.startswith(gate.UNMEASURED)


def test_a_missing_size_tool_reads_unmeasured_and_never_the_file_bytes(tmp_path, monkeypatch):
    # The hole-never-a-zero rule, on the one column where the wrong fallback is
    # available and plausible: substituting the file size would make `bytes` and
    # `code` agree by fiction on every machine without the Xcode tools.
    p = tmp_path / "d.macho"
    p.write_bytes(MACHO64 + b"\x00" * 4096)
    monkeypatch.setattr(gate.shutil, "which", lambda name: None)
    size, note = gate.text_bytes(p)
    assert size is None
    assert note.startswith(gate.UNMEASURED)
    assert "size(1)" in note
    assert str(p.stat().st_size) not in note


@pytest.mark.parametrize("body", [b"", b"#!/bin/sh\necho hi\n", b"MZ\x90\x00"])
def test_something_that_is_not_an_object_file_is_unmeasured(tmp_path, body):
    p = tmp_path / "e.bin"
    p.write_bytes(body)
    size, note = gate.text_bytes(p)
    assert size is None and note.startswith(gate.UNMEASURED)


def test_code_size_of_a_binary_that_is_not_there_is_a_hole(tmp_path):
    size, note = gate.text_bytes(tmp_path / "absent")
    assert size is None and note.startswith(gate.UNMEASURED)
