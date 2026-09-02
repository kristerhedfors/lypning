# The Swift study host, built the way assets/swift/Makefile builds its
# quickstart: the binding compiled to a module, then this driver linked against
# it and whichever liblypning this machine has. No SwiftPM, so nothing writes a
# .build/ here and there is no manifest to keep in step with the binding's.
here    := $(patsubst %/,%,$(dir $(abspath $(lastword $(MAKEFILE_LIST)))))
swift   := $(abspath $(here)/../../../src/lypning/assets/swift)
assets  := $(abspath $(swift)/..)
LYPNING_HOME ?= $(HOME)/.lypning

checkout_lib := $(assets)/rust/target/release-lib
ifneq ($(wildcard $(checkout_lib)/liblypning.so $(checkout_lib)/liblypning.dylib),)
LIBDIR ?= $(checkout_lib)
else
LIBDIR ?= $(LYPNING_HOME)/lib
endif

SWIFTC     ?= swiftc
SWIFTFLAGS ?= -O
link  := -L$(LIBDIR) -Xlinker -rpath -Xlinker $(abspath $(LIBDIR)) -llypning
BUILD := $(here)/build

.PHONY: all clean
all: run_swift

$(BUILD)/Lypning.o: $(swift)/Sources/Lypning/Lypning.swift $(swift)/Sources/CLypning/module.modulemap $(swift)/Sources/CLypning/shim.h
	@test -f "$(LIBDIR)/liblypning.so" -o -f "$(LIBDIR)/liblypning.dylib" || { \
	  echo "no liblypning under $(LIBDIR).  build it:  lypning build --lib"; exit 1; }
	@mkdir -p $(BUILD)
	$(SWIFTC) $(SWIFTFLAGS) -parse-as-library -module-name Lypning \
	  -I $(swift)/Sources/CLypning -emit-module -emit-module-path $(BUILD)/Lypning.swiftmodule \
	  -emit-object -o $@ $(swift)/Sources/Lypning/Lypning.swift

run_swift: main.swift $(BUILD)/Lypning.o
	$(SWIFTC) $(SWIFTFLAGS) -I $(BUILD) -I $(swift)/Sources/CLypning main.swift $(BUILD)/Lypning.o -o $@ $(link)

clean:
	rm -rf run_swift $(BUILD)
