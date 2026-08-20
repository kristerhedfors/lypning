# lypning-mp — the one entry point.
#
# Everything here delegates to scripts/build-micropython.sh, which is where the
# pinned versions, the musl-i386 build and the reasoning live. This file exists
# so `make` in this directory does the obvious thing.
#
#   make            build micropython/build/lypning-mp
#   make verify     build, then run the size/opens gate and the conformance battery
#   make gate       the gate alone, against an existing binary
#   make conformance  the corpus diff against the system CPython
#   make stock      build the benchmark control: unpatched upstream MicroPython
#   make bench      build both if needed, then run the performance benchmark
#   make clean      drop the built binary (keeps the musl + MicroPython cache)
#   make distclean  drop the cache too — the next build re-downloads

LYPNING_MP_DIR := $(dir $(abspath $(lastword $(MAKEFILE_LIST))))
ROOT := $(LYPNING_MP_DIR)..
BIN := $(LYPNING_MP_DIR)build/lypning-mp
STOCK := $(LYPNING_MP_DIR)build/micropython-stock
WORK ?= $(LYPNING_MP_DIR).build

.PHONY: all build verify gate conformance stock bench clean distclean

all: build

build:
	@bash $(ROOT)/scripts/build-micropython.sh

verify:
	@bash $(ROOT)/scripts/build-micropython.sh --verify

gate: $(BIN)
	@lypning gate $(BIN) --compare

conformance: $(BIN)
	@LYPNING_MP_BIN=$(abspath $(BIN)) lypning conformance

$(BIN):
	@bash $(ROOT)/scripts/build-micropython.sh

# The benchmark control. It is a SEPARATE checkout of the same pinned commit, so
# building it never invalidates the lypning-mp build's object files.
stock: $(STOCK)

$(STOCK):
	@bash $(ROOT)/scripts/build-micropython.sh --stock

bench: $(BIN) $(STOCK)
	@lypning bench --micropython

clean:
	rm -rf $(LYPNING_MP_DIR)build

# The musl build is the slow part (about a minute) and the MicroPython checkout
# is a download. Both are cached, so distclean is for "prove it works from
# nothing", not for routine use.
distclean:
	rm -rf $(LYPNING_MP_DIR)build $(WORK)
