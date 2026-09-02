# Thin wrappers over the CLI. Every target below is one `lypning` command and
# nothing else — if a target ever needs logic, the logic belongs in the CLI,
# where it is testable and available to people who do not use make.
#
# Pass extra flags through ARGS:
#     make conformance ARGS='--engine lypning --plan'
#     make bench ARGS='--startup'
#     make install ARGS=--dry-run

PYTHON  ?= python3

# PYTHONPATH=src on purpose: in a checkout this runs the tree you are editing
# rather than whatever wheel happens to be installed in the environment.
LYPNING ?= PYTHONPATH=src $(PYTHON) -m lypning

ARGS ?=

.DEFAULT_GOAL := help
.PHONY: help build test check conformance fuzz bench gate doctor install dist dist-check clean

help:
	@echo 'lypning — make targets (pass flags with ARGS=...)'
	@echo
	@echo '  build        compile the engines into ~/.lypning/bin'
	@echo '               (`make build ARGS=--rust` for the Rust core alone, seconds;'
	@echo '                the MicroPython tier needs a 32-bit toolchain and a network)'
	@echo '  test         the unit tests (pytest; dev extra)'
	@echo '  check        the four gates you owe before saying you are done'
	@echo '  conformance  run the corpus against CPython — MISMATCH must be 0'
	@echo '  fuzz         generate programs from the subset and diff them against CPython'
	@echo '  bench        time the four arms; deliberately not a CI gate'
	@echo '  gate         measure a built binary against the acceptance table'
	@echo '  doctor       every check with an opinion; non-zero on any FAIL'
	@echo '  install      wire the skill, hooks and shim into this project'
	@echo '               (`make install ARGS=--dry-run` prints the plan first)'
	@echo '  dist         build the sdist and wheel into dist/ (needs `pip install build`)'
	@echo '  dist-check   the same, then twine check and the wheel-contents assertion'
	@echo '  clean        remove Python build artifacts and caches'
	@echo
	@echo 'Also useful: $(LYPNING) status, doctor, route -c PROG, corpus --stats'

build:
	$(LYPNING) build $(ARGS)

test:
	$(PYTHON) -m pytest $(ARGS)

conformance:
	$(LYPNING) conformance $(ARGS)

fuzz:
	$(LYPNING) fuzz $(ARGS)

doctor:
	$(LYPNING) doctor $(ARGS)

# CLAUDE.md's "before you say you are done", in the order it lists them. The
# conformance arms are named rather than left to discovery: on a machine where
# the MicroPython tier happens to be built, a bare run ends at MISMATCH 2 on a
# defect this checkout did not introduce (docs/LYPNING.md §6), and a gate that
# is red before you start is a gate you learn to ignore. Run `make conformance`
# with no arguments to see the whole battery including that one.
check:
	$(LYPNING) build --rust
	$(LYPNING) conformance --engine lypning --engine mixture
	$(LYPNING) doctor
	@git status --short

bench:
	$(LYPNING) bench $(ARGS)

gate:
	$(LYPNING) gate $(ARGS)

install:
	$(LYPNING) install $(ARGS)

# Needs `pip install build`. Not `pip wheel`: Debian's patched setuptools makes
# that fail with `AttributeError: install_layout` on the system interpreter,
# which is exactly the interpreter a checkout is most likely to be built with.
dist:
	$(PYTHON) -m build $(ARGS)

# What CI runs before it trusts an artefact, and what you run before an upload.
# Needs `pip install build twine`; both are dev tools and neither may ever
# become a runtime dependency.
#
# `rm -rf dist` first, and not for tidiness: `twine check dist/*` reads whatever
# is in the directory, so last release's artefacts sitting alongside this one's
# turn a check of what you are about to upload into a check of what you already
# did.
#
# The last line is the one with teeth. `include_package_data = true` means a
# careless MANIFEST.in entry puts cargo's `target/` — a gigabyte of object files
# `pip uninstall` has never heard of — inside the wheel, and nothing else in
# this repository would notice. It stays a one-liner here rather than becoming a
# `lypning` subcommand: it is a claim about the distribution, so it cannot live
# in a module the distribution ships.
dist-check:
	rm -rf dist
	$(MAKE) dist
	$(PYTHON) -m twine check dist/*
	@$(PYTHON) -c "import glob, zipfile; bad = sorted(n for w in glob.glob('dist/*.whl') for n in zipfile.ZipFile(w).namelist() if '__pycache__' in n or '/target/' in n or '/build/' in n or '/.build/' in n or '/.swiftpm/' in n or '/node_modules/' in n or n.endswith(('.pyc', '.o', '.a', '.rlib', '.so', '.dylib', '.node', '.class'))); print('wheel: no build output' if not bad else 'wheel CONTAMINATED with build output: ' + ', '.join(bad[:5])); raise SystemExit(bool(bad))"

# Engine builds are NOT removed here: assets/rust/target is minutes of cargo and
# assets/micropython/build is a toolchain plus a network away. Drop those with
# `rm -rf src/lypning/assets/rust/target src/lypning/assets/micropython/build`,
# and the installed binaries with `rm -rf ~/.lypning`.
clean:
	rm -rf dist build src/*.egg-info .pytest_cache
	find . -name __pycache__ -type d -prune -exec rm -rf {} +
