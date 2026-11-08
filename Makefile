PYTHON ?= python3
PYTHON_SOURCES := SOURCE/head_turn_face_unlock SCRIPTS TESTS
SHELL_SOURCES := SCRIPTS/INSTALL.sh SCRIPTS/UNINSTALL.sh SOURCE/ENTRY/face-sudo

.PHONY: test lint check

# Unit and integration tests: no camera, no root.
test:
	$(PYTHON) -I -B -m unittest discover -s TESTS -t TESTS -p 'test_*.py'

# Each tool runs when it is installed and is skipped with a note otherwise.
lint:
	$(PYTHON) -I -B -c 'import sys; [compile(open(path, encoding="utf-8").read(), path, "exec") for path in sys.argv[1:]]' \
		$(wildcard SOURCE/head_turn_face_unlock/*.py SCRIPTS/*.py TESTS/*.py) SOURCE/ENTRY/service SOURCE/ENTRY/pam-helper SOURCE/ENTRY/head-turn-face-unlock
	@if command -v ruff > /dev/null; then ruff check $(PYTHON_SOURCES); else echo "ruff not installed: skipped"; fi
	@if command -v mypy > /dev/null; then mypy SOURCE/head_turn_face_unlock; else echo "mypy not installed: skipped"; fi
	@if command -v shellcheck > /dev/null; then shellcheck $(SHELL_SOURCES); else echo "shellcheck not installed: skipped"; fi

check: lint test
