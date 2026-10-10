VENV ?= .venv
BIN  := $(VENV)/bin

.PHONY: help venv deps up deploy down test lint ps shell

help: ## Show targets
	@grep -E '^[a-z-]+:.*##' $(MAKEFILE_LIST) | awk -F':.*## ' '{printf "  %-8s %s\n", $$1, $$2}'

$(BIN)/ansible-playbook: requirements-dev.txt
	python3 -m venv $(VENV)
	$(BIN)/pip install -q -r requirements-dev.txt
	touch $@

venv: $(BIN)/ansible-playbook ## Create .venv with pinned Ansible, linters and pytest

deps: venv ## Install the venv and Ansible collections
	$(BIN)/ansible-galaxy collection install -r requirements.yml

up: venv ## Build the image and bring the lab up (idempotent)
	$(BIN)/ansible-playbook playbooks/site.yml

deploy: up ## Alias for up until the service roles exist

down: ## Remove all lab containers and networks
	$(BIN)/ansible-playbook playbooks/destroy.yml

test: ## Run the integration tests against the running lab
	$(BIN)/pytest -v tests/integration

lint: ## yamllint + ansible-lint + shellcheck
	$(BIN)/yamllint .
	$(BIN)/ansible-lint
	shellcheck docker/node/entrypoint.sh docker/node/sos-apply

ps: ## Show lab containers
	docker compose -p sync-or-swim ps

shell: ## Open a shell in a node: make shell N=mysql-1
	docker exec -it $(N) bash
