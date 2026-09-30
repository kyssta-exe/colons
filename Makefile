.DEFAULT_GOAL := help
VENV ?= $(CURDIR)/colons/.venv
export PATH := $(VENV)/bin:$(PATH)
.PHONY: help install dev test lint web serve chat doctor logs docker clean
help:
	@printf '%s\n' 'make install  Install dependencies, UI, and Chromium' 'make dev      Install with development dependencies' 'make serve    Run Colons on localhost' 'make test     Run the Python suite' 'make lint     Check the backend' 'make web      Build the web UI' 'make docker   Start the Docker Compose stack'
install:
	COLONS_VENV="$(VENV)" ./scripts/install.sh
dev:
	COLONS_VENV="$(VENV)" ./scripts/install.sh --dev
serve:
	COLONS_VENV="$(VENV)" ./scripts/run.sh
test lint web chat doctor logs docker clean:
	$(MAKE) -C colons $@
