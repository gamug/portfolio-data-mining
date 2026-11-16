# Dev container image for portfolio-data-mining.
#
# Equivalent of the previous devcontainer.json "image" + "features" setup:
#   - base image:  mcr.microsoft.com/devcontainers/python:1-3.12-bookworm
#   - uv feature:  ghcr.io/va-h/devcontainers-features/uv:1 -> uv is copied from Astral's
#                  official image instead (same effect: `uv`/`uvx` on PATH).
#
# Runtime concerns (GPU passthrough, --env-file, the thesis bind mount and the
# postCreateCommand) can't live in a Dockerfile; they stay in devcontainer.json.
# GPU note: news_nlp's torch wheels (cu124) bundle the CUDA runtime, so this image does
# not need to be CUDA-based - only the host NVIDIA driver/devices must reach the container.
FROM mcr.microsoft.com/devcontainers/python:1-3.12-bookworm

# uv (was the va-h/devcontainers-features/uv feature, locked at 1.1.4). Pin a uv version
# here for reproducible builds, e.g. ghcr.io/astral-sh/uv:0.5.x
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /usr/local/bin/

# The base image's non-root user; matches the devcontainer default.
USER vscode
