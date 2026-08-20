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
.PHONY: help build test conformance bench gate install dist clean

help:
	@echo 'lypning — make targets (pass flags with ARGS=...)'
	@echo
	@echo '  build        compile the engines into ~/.lypning/bin'
	@echo '               (`make build ARGS=--rust` for the Rust core alone, ~18s;'
	@echo '                the MicroPython tier needs a 32-bit toolchain and a network)'
	@echo '  test         the unit tests (pytest; dev extra)'
	@echo '  conformance  run the corpus against CPython — MISMATCH must be 0'
	@echo '  bench        time the four arms; deliberately not a CI gate'
	@echo '  gate         measure a built binary against the acceptance table'
	@echo '  install      wire the skill, hooks and shim into this project'
	@echo '               (`make install ARGS=--dry-run` prints the plan first)'
	@echo '  dist         build the sdist and wheel into dist/ (needs `pip install build`)'
	@echo '  clean        remove Python build artifacts and caches'
	@echo
	@echo 'Also useful: $(LYPNING) status, doctor, route -c PROG, corpus --stats'

build:
	$(LYPNING) build $(ARGS)

test:
	$(PYTHON) -m pytest $(ARGS)

conformance:
	$(LYPNING) conformance $(ARGS)

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

# Engine builds are NOT removed here: assets/rust/target is minutes of cargo and
# assets/micropython/build is a toolchain plus a network away. Drop those with
# `rm -rf src/lypning/assets/rust/target src/lypning/assets/micropython/build`,
# and the installed binaries with `rm -rf ~/.lypning`.
clean:
	rm -rf dist build src/*.egg-info .pytest_cache
	find . -name __pycache__ -type d -prune -exec rm -rf {} +
