# Makefile — the no-SwiftPM path: two swiftc lines against whichever lypning
# this machine actually has. Mirrors ../examples/c/Makefile: prefer the
# checkout's freshly built library, fall back to the one `lypning build --lib`
# installed under $LYPNING_HOME (~/.lypning), and say which on every link.
#
#   make                 build quickstart and run it on one program
#   make quickstart      build only
#   make LIBDIR=...      link something else entirely
#
# Two swiftc lines, not one, because `quickstart/main.swift` imports Lypning as
# a MODULE: a single invocation would compile both files into one module and
# swiftc would warn that the import is of the module being built. The first
# line emits the module and its object; the second links the program against
# them. `swift build` does the same thing with more machinery and writes
# .build/ beside this file (use `--scratch-path` in a read-only wheel tree).

here    := $(patsubst %/,%,$(dir $(abspath $(lastword $(MAKEFILE_LIST)))))
assets  := $(abspath $(here)/..)

LYPNING_HOME ?= $(HOME)/.lypning

checkout_lib := $(assets)/rust/target/release-lib
state_lib    := $(LYPNING_HOME)/lib

ifneq ($(wildcard $(checkout_lib)/liblypning.so $(checkout_lib)/liblypning.dylib),)
default_lib := $(checkout_lib)
from        := source checkout
else
default_lib := $(state_lib)
from        := installed tree
endif

LIBDIR ?= $(default_lib)
ifneq ($(LIBDIR),$(default_lib))
from := LIBDIR override
endif

SWIFTC     ?= swiftc
SWIFTFLAGS ?= -O
# -I Sources/CLypning finds module.modulemap, which finds the shared header.
# -rpath so the binary runs from anywhere without an environment variable.
link := -L$(LIBDIR) -Xlinker -rpath -Xlinker $(abspath $(LIBDIR)) -llypning

BUILD := $(here)/build

all: run

$(BUILD)/Lypning.o: Sources/Lypning/Lypning.swift Sources/CLypning/module.modulemap Sources/CLypning/shim.h
	@test -f "$(LIBDIR)/liblypning.so" -o -f "$(LIBDIR)/liblypning.dylib" || { \
	  echo "no liblypning under $(LIBDIR)."; \
	  echo "build it:  lypning build --lib"; \
	  echo "or point at a copy you already have:  make LIBDIR=..."; \
	  exit 1; }
	@mkdir -p $(BUILD)
	$(SWIFTC) $(SWIFTFLAGS) -parse-as-library -module-name Lypning \
	  -I Sources/CLypning -emit-module -emit-module-path $(BUILD)/Lypning.swiftmodule \
	  -emit-object -o $@ Sources/Lypning/Lypning.swift

quickstart: Sources/quickstart/main.swift $(BUILD)/Lypning.o
	$(SWIFTC) $(SWIFTFLAGS) -I $(BUILD) -I Sources/CLypning \
	  Sources/quickstart/main.swift $(BUILD)/Lypning.o -o $@ $(link)
	@echo "$@: library $(LIBDIR) ($(from))"

run: quickstart
	./quickstart "print(sum(range(10)))"

clean:
	rm -rf quickstart $(BUILD)

.PHONY: all run clean
