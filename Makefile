# Convenience wrapper: run any lab target from the repo root,
# e.g. `make host-check` == `make -C containerlab/vlabzone host-check`.
LAB_DIR := containerlab/vlabzone

.DEFAULT_GOAL := help
.PHONY: help
help:
	@$(MAKE) --no-print-directory -C $(LAB_DIR) help

%:
	@$(MAKE) --no-print-directory -C $(LAB_DIR) $@
