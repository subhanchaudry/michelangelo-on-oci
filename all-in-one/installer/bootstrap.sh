#!/usr/bin/env bash
# Repair and verify bootstrap dependencies after VCN DNS becomes usable.
# Original cloud-init status/logs are preserved; successful package checks gate installation.
set -euo pipefail
if [[ $(id -u) != 0 ]]; then
  echo "Run bootstrap.sh as root" >&2
  exit 1
fi
export DEBIAN_FRONTEND=noninteractive
retry() {
  local attempt
  for attempt in $(seq 1 20); do
    if "$@"; then return 0; fi
    if [[ $attempt == 20 ]]; then
      echo "Bootstrap package operation failed after 20 attempts" >&2
      return 1
    fi
    echo "Waiting for package repository/DNS readiness; retry $attempt/20" >&2
    sleep 20
  done
}
# Error-Mode=any prevents apt update returning success with every repository unreachable.
retry apt-get -o Acquire::Retries=3 -o DPkg::Lock::Timeout=300 -o APT::Update::Error-Mode=any update
retry apt-get -o Acquire::Retries=3 -o DPkg::Lock::Timeout=300 install --yes --no-install-recommends \
  docker.io docker-buildx git curl unzip openssl python3-pip python3-venv
systemctl enable --now docker
usermod -aG docker ubuntu
for command in docker git curl unzip openssl python3; do
  command -v "$command" >/dev/null || { echo "Missing bootstrap command: $command" >&2; exit 1; }
done
python3 -m pip --version >/dev/null
python3 -c 'import venv, ssl, urllib.request'
docker info >/dev/null
docker buildx version >/dev/null
echo "Bootstrap package dependencies and Docker Buildx verified"
