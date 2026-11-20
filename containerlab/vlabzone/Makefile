# vLabZone – containerlab test environment for hardware-pool access
# Run `make help` for an overview.

SHELL := /bin/bash
.DEFAULT_GOAL := help

# Per-host settings (rendered by Ansible or copied from .env.example)
-include .env
# Pinned version on purpose: no "newer version available" banner
export CLAB_VERSION_CHECK := disable
export EDGE_IMPL EDGE_MODE VLAB_PROFILE VLAB_ROUTEROS_ENABLED ROUTEROS_VERSION VLAB_MGMT_SUBNET VLAB_MGMT_SUBNET6

VERSION          := $(shell cat VERSION)
EDGE_IMPL        ?= linux
EDGE_MODE        ?= vrf
VLAB_PROFILE     ?= base
ROUTEROS_VERSION ?= 7.24.6
APT_MIRROR       ?=

LAB_NAME  := hwpool
TOPO      ?= topology/hwpool.clab.yml
SMOKE_TOPO := topology/smoke.clab.yml

# containerlab is installed setuid: members of clab_admins (added by Ansible) need no sudo.
# Root or clab_admins -> no sudo; everyone else falls back to sudo.
SUDO  := $(if $(or $(filter 0,$(shell id -u)),$(filter clab_admins,$(shell id -nG))),,sudo)
CLAB  := $(SUDO) containerlab
VENV  := .venv
PY    := $(VENV)/bin/python

# Build order matters: every role image is built FROM hwpool/base
IMAGES := router edge workspace device serial mgmt broker

BUILD_ARGS := --build-arg VLAB_VERSION=$(VERSION) \
              --build-arg APT_MIRROR=$(APT_MIRROR) \
              $(if $(HTTP_PROXY),--build-arg http_proxy=$(HTTP_PROXY)) \
              $(if $(HTTPS_PROXY),--build-arg https_proxy=$(HTTPS_PROXY))

.PHONY: help
help: ## Show this help
	@grep -hE '^[a-zA-Z0-9_-]+:.*?## ' $(MAKEFILE_LIST) | \
	  awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-16s\033[0m %s\n", $$1, $$2}'
	@echo
	@echo "  Current: EDGE_IMPL=$(EDGE_IMPL) EDGE_MODE=$(EDGE_MODE) PROFILE=$(VLAB_PROFILE) VERSION=$(VERSION)"

.PHONY: host-check
host-check: ## Check host prerequisites (kernel modules, docker, containerlab, resources)
	@scripts/preflight.sh --stage post --profile $(VLAB_PROFILE) \
	  $(if $(filter true,$(VLAB_ROUTEROS_ENABLED)),--routeros)

.PHONY: build
build: ## Build all container images (hwpool/<role>:<version> and :latest)
	docker build $(BUILD_ARGS) -t hwpool/base:$(VERSION) -t hwpool/base:latest images/base
	@for img in $(IMAGES); do \
	  echo ">>> building hwpool/$$img:$(VERSION)"; \
	  docker build $(BUILD_ARGS) -t hwpool/$$img:$(VERSION) -t hwpool/$$img:latest images/$$img || exit 1; \
	done

.PHONY: build-routeros
build-routeros: ## Build the RouterOS CHR image via vrnetlab (needs /dev/kvm at runtime)
	images/routeros/build.sh --version $(ROUTEROS_VERSION) $(if $(ROUTEROS_SHA256),--sha256 $(ROUTEROS_SHA256))

.PHONY: images
images: ## List the locally built lab images
	@docker image ls --filter 'reference=hwpool/*' --filter 'reference=vrnetlab/*'

.PHONY: venv
venv: $(VENV)/.done ## Create the Python venv for the test suite
$(VENV)/.done: requirements.txt
	python3 -m venv $(VENV)
	$(VENV)/bin/pip install -q --upgrade pip
	$(VENV)/bin/pip install -q -r requirements.txt
	touch $@

.PHONY: smoke
smoke: ## Deploy a 2-node smoke lab, check VLAN/VRF/WireGuard support, destroy it
	scripts/smoke.sh

.PHONY: up
up: _require-topo ## Deploy the lab topology
	$(CLAB) deploy -t $(TOPO) --reconfigure

.PHONY: down
down: _require-topo ## Destroy the lab topology
	$(CLAB) destroy -t $(TOPO) --cleanup

.PHONY: reset
reset: down up ## Destroy and redeploy the lab (back to baseline)

.PHONY: inspect
inspect: _require-topo ## Show the running lab nodes
	$(CLAB) inspect -t $(TOPO)

.PHONY: test
test: venv ## Run the test suite (respects EDGE_IMPL / EDGE_MODE)
	$(PY) -m pytest tests

.PHONY: clean
clean: ## Remove the venv and local results (images stay)
	rm -rf $(VENV) .pytest_cache
	find results -mindepth 1 ! -name .gitkeep -delete

.PHONY: _require-topo
_require-topo:
	@test -f $(TOPO) || { echo "Topology $(TOPO) does not exist yet (comes with stage 1). Use 'make smoke' for now."; exit 1; }
