# The two native hosts, linked against whichever liblypning this machine has.
# Mirrors assets/examples/{c,cpp}/Makefile: prefer the checkout's freshly built
# library, fall back to the one `lypning build --lib` installed.
here    := $(patsubst %/,%,$(dir $(abspath $(lastword $(MAKEFILE_LIST)))))
assets  := $(abspath $(here)/../../src/lypning/assets)
LYPNING_HOME ?= $(HOME)/.lypning

checkout_lib := $(assets)/rust/target/release-lib
checkout_inc := $(assets)/include
ifneq ($(wildcard $(checkout_lib)/liblypning.so),)
LIBDIR ?= $(checkout_lib)
INCDIR ?= $(checkout_inc)
else
LIBDIR ?= $(LYPNING_HOME)/lib
INCDIR ?= $(LYPNING_HOME)/include
endif

CC       ?= cc
CXX      ?= g++
CFLAGS   ?= -std=c11 -O2 -Wall -Wextra
CXXFLAGS ?= -std=c++17 -O2 -Wall -Wextra
link     := -L$(LIBDIR) -Wl,-rpath,$(LIBDIR) -llypning

.PHONY: all clean
all: run_c run_cpp

run_c: run_c.c capture.h
	$(CC) $(CFLAGS) -I$(INCDIR) -I$(here) run_c.c -o $@ $(link)

run_cpp: run_cpp.cpp capture.h
	$(CXX) $(CXXFLAGS) -I$(INCDIR) -I$(here) run_cpp.cpp -o $@ $(link)

clean:
	rm -f run_c run_cpp
