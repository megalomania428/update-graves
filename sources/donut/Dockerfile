# cspell:ignore rustup noninteractive
# Toolchain image of one target kind, built on the runner by scripts/build.sh.
ARG BASE_IMAGE=mirror.gcr.io/ubuntu:22.04
FROM ${BASE_IMAGE}
ARG TOOLCHAIN=linux
# RUSTUP_HOME is a cached mount at run time: the image keeps only rustup itself.
ENV DEBIAN_FRONTEND=noninteractive \
  CARGO_HOME=/usr/local/cargo \
  RUSTUP_HOME=/cache/rustup \
  CARGO_INCREMENTAL=0 \
  PATH=/usr/local/cargo/bin:${PATH}
COPY scripts/image.sh /usr/local/lib/donutbrowser/image.sh
RUN bash /usr/local/lib/donutbrowser/image.sh
