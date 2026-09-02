# Every study driver that has a build step, linked against whichever liblypning
# this machine has; `make -C study/hosts` is what run_all.sh expects to have run.
# Mirrors assets/examples/{c,cpp}/Makefile: prefer the checkout's freshly built
# library, fall back to the one `lypning build --lib` installed. Both names are
# looked for, because the shared library is liblypning.dylib on macOS and
# liblypning.so everywhere else, and a Makefile that knew one of them would
# silently link yesterday's installed copy on the other platform.
here    := $(patsubst %/,%,$(dir $(abspath $(lastword $(MAKEFILE_LIST)))))
assets  := $(abspath $(here)/../../src/lypning/assets)
LYPNING_HOME ?= $(HOME)/.lypning

checkout_lib := $(assets)/rust/target/release-lib
checkout_inc := $(assets)/include
ifneq ($(wildcard $(checkout_lib)/liblypning.so $(checkout_lib)/liblypning.dylib),)
LIBDIR ?= $(checkout_lib)
INCDIR ?= $(checkout_inc)
else
LIBDIR ?= $(LYPNING_HOME)/lib
INCDIR ?= $(LYPNING_HOME)/include
endif

# $(wildcard) rather than a bare path: a missing header or library must reach
# the message below, not make's own "No rule to make target".
header := $(wildcard $(INCDIR)/lypning.h)
lib    := $(wildcard $(LIBDIR)/liblypning.so $(LIBDIR)/liblypning.dylib)

CC       ?= cc
CXX      ?= g++
CFLAGS   ?= -std=c11 -O2 -Wall -Wextra
CXXFLAGS ?= -std=c++17 -O2 -Wall -Wextra
link     := -L$(LIBDIR) -Wl,-rpath,$(LIBDIR) -llypning

.PHONY: all clean check run_rust run_go run_swift node
all: run_c run_cpp run_rust run_go run_swift node

# One message, naming the command that fixes it. An empty -I compiles against
# whatever lypning.h the include path already had, and an empty -L links
# against nothing at all; either is a study that measured the wrong library.
check:
	@test -n "$(header)" || { \
	  echo "no lypning.h under $(INCDIR) — run \`lypning build --lib\`, or make INCDIR=... LIBDIR=..."; \
	  exit 1; }
	@test -n "$(lib)" || { \
	  echo "no liblypning under $(LIBDIR) — run \`lypning build --lib\`, or make INCDIR=... LIBDIR=..."; \
	  exit 1; }

run_c: run_c.c capture.h $(header) $(lib) | check
	$(CC) $(CFLAGS) -I$(INCDIR) -I$(here) run_c.c -o $@ $(link)

run_cpp: run_cpp.cpp capture.h $(header) $(lib) | check
	$(CXX) $(CXXFLAGS) -I$(INCDIR) -I$(here) run_cpp.cpp -o $@ $(link)

# The Rust driver compiles the crate and links no library; it is here so one
# make builds every driver run_all.sh names.
run_rust:
	cargo build --release --manifest-path $(here)/run_rust/Cargo.toml

# cgo finds the checkout's library through the binding's own #cgo directives,
# so CGO_LDFLAGS is set only when LIBDIR points elsewhere (an installed copy,
# or `make LIBDIR=...`), which keeps `make LIBDIR=...` meaning the same thing
# for every driver without macOS ld warning about a duplicate -rpath. A
# directory named run_go exists, so the target is phony on purpose.
ifeq ($(LIBDIR),$(checkout_lib))
go_ldflags :=
else
go_ldflags := -L$(LIBDIR) -Wl,-rpath,$(LIBDIR)
endif
run_go: | check
	@command -v go >/dev/null || { echo "no go on PATH: the Go driver needs a Go toolchain"; exit 1; }
	cd $(here)/run_go && CGO_LDFLAGS="$(go_ldflags)" go build -o run_go .

# Plain swiftc, no SwiftPM: run_swift/Makefile compiles the binding to a module
# and links this driver against it.
run_swift: | check
	@command -v swiftc >/dev/null || { echo "no swiftc on PATH: the Swift driver needs a Swift toolchain"; exit 1; }
	$(MAKE) -C $(here)/run_swift LIBDIR=$(LIBDIR)

# The Node driver loads the addon in assets/node, which is its own cargo build
# and links the runtime statically: `lypning build --lib` does not produce it.
node:
	cargo build --release --manifest-path $(assets)/node/Cargo.toml

# The LuaJIT driver has no build step: lypning.lua reads the header at load.

clean:
	rm -f run_c run_cpp run_go/run_go
	$(MAKE) -C $(here)/run_swift clean
