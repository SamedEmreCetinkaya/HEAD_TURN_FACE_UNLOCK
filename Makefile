# Head-turn face unlock: quality gates.
#
# One source of truth: the pre-push hook, a CI runner and a person call the same targets,
# so a command changes here and every caller follows.
#
#   make ci        compile, lint (mypy, shellcheck), all tests with coverage floors  <- pre-push runs this
#   make test      all tests without coverage; needs no development tools
#   make lint      compile every file, mypy on the package, scripts and tools, shellcheck on every shell script
#   make coverage  tests with coverage; a module below its floor fails, a module above raises its floor
#   make coverage-floor  set every floor to the last measurement (deliberate lowering, explain it in the commit)
#   make doctor    which development tools are installed and how to get the missing ones
#   make hooks     run `make ci` before every push (once per clone)
#   make help      this list

SHELL := /bin/bash
.SHELLFLAGS := -eu -o pipefail -c
PYTHON ?= python3
SHELL_SOURCES := SCRIPTS/INSTALL.sh SCRIPTS/UNINSTALL.sh SOURCE/ENTRY/face-sudo .githooks/pre-push
PYTHON_ENTRIES := SOURCE/ENTRY/service SOURCE/ENTRY/pam-helper SOURCE/ENTRY/head-turn-face-unlock

.PHONY: ci test lint coverage coverage-floor doctor hooks help

help:
	@sed -n 's/^#   //p' Makefile

ci: lint coverage
	@echo "── ci green ───────────────────────────────"

test:
	@echo "── tests ──────────────────────────────────"
	@$(PYTHON) -I -B -m unittest discover -s TESTS -t TESTS -p 'test_*.py'

lint:
	@echo "── lint ───────────────────────────────────"
	@$(PYTHON) -I -B -c 'import sys; [compile(open(path, encoding="utf-8").read(), path, "exec") for path in sys.argv[1:]]' \
		$(wildcard SOURCE/head_turn_face_unlock/*.py SCRIPTS/*.py TESTS/*.py TOOLS/CI/*.py) $(PYTHON_ENTRIES)
	@command -v mypy > /dev/null || { echo "mypy is missing: make doctor"; exit 1; }
	@mypy SOURCE/head_turn_face_unlock SCRIPTS/*.py TOOLS/CI/*.py
	@command -v shellcheck > /dev/null || { echo "shellcheck is missing: make doctor"; exit 1; }
	@shellcheck $(SHELL_SOURCES)
	@if command -v ruff > /dev/null; then ruff check SOURCE SCRIPTS TESTS TOOLS; else echo "ruff not installed (optional): skipped"; fi

coverage:
	@echo "── tests with coverage ────────────────────"
	@$(PYTHON) -I -B TOOLS/CI/COVERAGE.py run

coverage-floor:
	@$(PYTHON) -I -B TOOLS/CI/COVERAGE.py reset

doctor:
	@for tool in mypy shellcheck; do \
	   if command -v $$tool > /dev/null; then echo "$$tool: $$($$tool --version | grep -m 1 -o '[0-9][0-9.]*')"; \
	   else echo "$$tool: missing"; fi; done
	@if $(PYTHON) -I -c 'import coverage' 2> /dev/null; then echo "coverage: $$($(PYTHON) -I -c 'import coverage; print(coverage.__version__)')"; \
	 else echo "coverage: missing"; fi
	@echo "Debian and Ubuntu: sudo apt install python3-coverage mypy shellcheck"
	@echo "Fedora: sudo dnf install python3-coverage python3-mypy ShellCheck"
	@echo "Arch Linux: sudo pacman -S python-coverage mypy shellcheck"

hooks:
	@git config core.hooksPath .githooks
	@echo "pre-push now runs make ci (skip once on purpose with: git push --no-verify)"
