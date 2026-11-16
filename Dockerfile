# Dev container image for portfolio-data-mining: the devcontainers python 3.12 base plus uv.
#
# Runtime concerns (GPU passthrough, --env-file, the thesis bind mount, postCreateCommand)
# cannot live in a Dockerfile; they are in devcontainer.json, which also documents the GPU
# requirements (the cu124 torch wheels bundle the CUDA runtime, so no CUDA base is needed).
#
# The base image's user is root; the dev container connects as `vscode` through the
# `remoteUser` in the image's devcontainer metadata, so no USER instruction is needed here.
FROM mcr.microsoft.com/devcontainers/python:1-3.12-bookworm

# uv, pinned to the host's uv version (the digest makes the build reproducible).
# To bump: pull the new tag, copy its digest, and update both below.
COPY --from=ghcr.io/astral-sh/uv:0.12.1@sha256:cf4eedcaa81655197f625739489effcbe71b61ceb1506f332c3facae5deceded /uv /uvx /usr/local/bin/
