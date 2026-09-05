# Makefile

.DEFAULT_GOAL := help

## Container Configuration
IMAGE ?= html2pdf
DEV_TAG ?= dev
HOST ?= 127.0.0.1
PORT ?= 8080
HTML2PDF_TOKEN ?=
DOCKER_BUILD_ARGS ?=
DOCKER_RUN_ARGS ?=

# Default tag prefix. Override with an empty value for unprefixed tags.
VERSION_PREFIX ?= v

##@ Tagging

# Find the latest tag with the configured prefix, or use 0.0.0 when none exists.
LATEST_TAG = $(shell git tag --list "$(VERSION_PREFIX)*" --sort=-v:refname | head -n 1)
VERSION = $(shell [ -n "$(LATEST_TAG)" ] && echo $(LATEST_TAG) | sed "s/^$(VERSION_PREFIX)//" || echo "0.0.0")
BUILD_VERSION ?= $(if $(LATEST_TAG),$(LATEST_TAG),dev)

.PHONY: patch
patch: ## Create a new patch release (x.y.Z+1).
	@NEW_VERSION=$$(echo "$(VERSION)" | awk -F. '{printf "%d.%d.%d", $$1, $$2, $$3+1}') && \
	git tag "$(VERSION_PREFIX)$${NEW_VERSION}" && \
	echo "Tagged $(VERSION_PREFIX)$${NEW_VERSION}"

.PHONY: minor
minor: ## Create a new minor release (x.Y+1.0).
	@NEW_VERSION=$$(echo "$(VERSION)" | awk -F. '{printf "%d.%d.0", $$1, $$2+1}') && \
	git tag "$(VERSION_PREFIX)$${NEW_VERSION}" && \
	echo "Tagged $(VERSION_PREFIX)$${NEW_VERSION}"

.PHONY: major
major: ## Create a new major release (X+1.0.0).
	@NEW_VERSION=$$(echo "$(VERSION)" | awk -F. '{printf "%d.0.0", $$1+1}') && \
	git tag "$(VERSION_PREFIX)$${NEW_VERSION}" && \
	echo "Tagged $(VERSION_PREFIX)$${NEW_VERSION}"

.PHONY: tag
tag: ## Show the latest tag.
	@echo "Latest version: $(LATEST_TAG)"

.PHONY: push
push: ## Push tags to the configured remote.
	git push --tags

##@ Development

.PHONY: test
test: ## Run the unit tests.
	python3 -m unittest -v

.PHONY: build
build: ## Build the development container image.
	docker build $(DOCKER_BUILD_ARGS) --build-arg VERSION="$(BUILD_VERSION)" -t $(IMAGE):$(DEV_TAG) .

.PHONY: dev
dev: build ## Build and run the service locally.
	docker run --rm $(DOCKER_RUN_ARGS) \
		-p $(HOST):$(PORT):8080 \
		$(IMAGE):$(DEV_TAG)

.PHONY: dev-auth
dev-auth: build ## Build and run locally with bearer-token authentication.
	@test -n "$(HTML2PDF_TOKEN)" || { echo "Set HTML2PDF_TOKEN first" >&2; exit 1; }
	docker run --rm $(DOCKER_RUN_ARGS) \
		-p $(HOST):$(PORT):8080 \
		-e HTML2PDF_TOKEN="$(HTML2PDF_TOKEN)" \
		$(IMAGE):$(DEV_TAG)

.PHONY: compose
compose: ## Run the development stack with Docker Compose.
	HTML2PDF_BUILD_VERSION="$(BUILD_VERSION)" docker compose up --build

.PHONY: compose-auth
compose-auth: ## Run the development stack with bearer-token authentication.
	@test -n "$(HTML2PDF_TOKEN)" || { echo "Set HTML2PDF_TOKEN first" >&2; exit 1; }
	HTML2PDF_BUILD_VERSION="$(BUILD_VERSION)" HTML2PDF_TOKEN="$(HTML2PDF_TOKEN)" docker compose up --build

.PHONY: stop
stop: ## Stop the Docker Compose stack.
	docker compose down

.PHONY: clean
clean: ## Remove the development container image.
	-docker image rm $(IMAGE):$(DEV_TAG)

##@ General

.PHONY: help
help: ## Display this help.
	@awk 'BEGIN {FS = ":.*##"; printf "\nUsage:\n  make \033[36m<target>\033[0m\n"} /^[a-zA-Z_0-9-]+:.*?##/ { printf "  \033[36m%-20s\033[0m %s\n", $$1, $$2 } /^##@/ { printf "\n\033[1m%s\033[0m\n", substr($$0, 5) }' $(MAKEFILE_LIST)
