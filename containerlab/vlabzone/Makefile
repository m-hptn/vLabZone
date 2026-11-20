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

# One lab per edge mode, generated from config/setups.yaml (topology/gen.py).
# Both can run at the same time: make up-all / test-all / down-all
MODES     := vrf pbr
LAB_NAME   = vlabzone-$(EDGE_MODE)
TOPO       = topology/vlabzone-$(EDGE_MODE).clab.yml
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
	@echo "  Current: EDGE_IMPL=$(EDGE_IMPL) EDGE_MODE=$(EDGE_MODE) (lab $(LAB_NAME)) PROFILE=$(VLAB_PROFILE) VERSION=$(VERSION)"

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

.PHONY: topology
topology: venv ## Regenerate topology/vlabzone-{vrf,pbr}.clab.yml from config/setups.yaml
	$(PY) topology/gen.py

.PHONY: up
up: topology ## Deploy the lab of EDGE_MODE (vrf|pbr) and wait until all nodes are ready
	$(CLAB) deploy -t $(TOPO) --reconfigure
	scripts/wait-ready.sh $(LAB_NAME)

.PHONY: down
down: ## Destroy the lab of EDGE_MODE
	$(CLAB) destroy -t $(TOPO) --cleanup

.PHONY: reset
reset: ## Destroy and redeploy the lab of EDGE_MODE (back to baseline)
	$(MAKE) --no-print-directory down
	$(MAKE) --no-print-directory up

.PHONY: inspect
inspect: ## Show the running nodes of the lab of EDGE_MODE
	$(CLAB) inspect -t $(TOPO)

.PHONY: up-all down-all test-all
up-all: ## Deploy both labs (vrf and pbr)
	@for m in $(MODES); do $(MAKE) --no-print-directory up EDGE_MODE=$$m || exit 1; done
down-all: ## Destroy both labs
	@for m in $(MODES); do $(MAKE) --no-print-directory down EDGE_MODE=$$m; done
test-all: venv ## Run the test suite against both labs
	@rc=0; for m in $(MODES); do echo ">>> EDGE_MODE=$$m"; EDGE_MODE=$$m $(PY) -m pytest tests $(PYTEST_ARGS) || rc=1; done; exit $$rc

.PHONY: test
test: venv ## Run the test suite against the lab of EDGE_MODE
	$(PY) -m pytest tests $(PYTEST_ARGS)

test-stage%: venv ## Run the tests of one stage, e.g. make test-stage1
	$(PY) -m pytest tests -m stage$* $(PYTEST_ARGS)

.PHONY: spike-up spike-down
SPIKE_TOPO := topology/spike-routeros.clab.yml
spike-up: ## Stage 1b: deploy the RouterOS trial lab (CHR, needs /dev/kvm + make build-routeros)
	$(CLAB) deploy -t $(SPIKE_TOPO) --reconfigure
spike-down: ## Stage 1b: destroy the RouterOS trial lab
	$(CLAB) destroy -t $(SPIKE_TOPO) --cleanup

.PHONY: pcap
pcap: ## Capture on a node: make pcap NODE=corp-fw IF=eth10 [SECONDS=30]
	@test -n "$(NODE)" -a -n "$(IF)" || { echo "usage: make pcap NODE=<node> IF=<interface> [SECONDS=30]"; exit 1; }
	@mkdir -p results
	docker exec clab-$(LAB_NAME)-$(NODE) timeout $(or $(SECONDS),30) tcpdump -i $(IF) -nn -U -w - > results/$(NODE)-$(IF)-$$(date +%Y%m%d-%H%M%S).pcap || true
	@ls -1t results/*.pcap | head -1

.PHONY: clean
clean: ## Remove the venv and local results (images stay)
	rm -rf $(VENV) .pytest_cache
	find results -mindepth 1 ! -name .gitkeep -delete

