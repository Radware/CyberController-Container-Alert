#!/usr/bin/env bash
# Assemble tested release installation bundles without running Docker.
# Usage: bash scripts/package_release.sh <prebuilt-watchdog.tar> <output-directory>
set -euo pipefail

if [ "$#" -ne 2 ]; then
    echo "Usage: $0 <prebuilt-watchdog.tar> <output-directory>" >&2
    exit 2
fi

repo="$(cd "$(dirname "$0")/.." && pwd)"
version="$(tr -d '[:space:]' < "$repo/VERSION")"
if ! [[ "$version" =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]]; then
    echo "Invalid VERSION: $version" >&2
    exit 1
fi

image="$(realpath "$1")"
test -f "$image" && test -s "$image" || { echo "Image archive missing or empty" >&2; exit 1; }
mkdir -p "$2"
out="$(cd "$2" && pwd)"
tmp="$(mktemp -d)"
trap 'rm -rf "$tmp"' EXIT

dir="$tmp/CyberController-Container-Alert"
mkdir -p "$dir/docs"
for file in install.sh uninstall.sh docker-compose.yaml VERSION \
            .env.example watchdog-config.yaml.example README.md DEPLOYMENT.md; do
    cp "$repo/$file" "$dir/$file"
done
cp "$repo"/docs/*.md "$dir/docs/"  # text-only references; never Python source

online="container-alert-v$version-online.tar.gz"
offline="container-alert-v$version-offline.tar.gz"

# Both packages have exactly the same installer and configuration templates.
tar -C "$tmp" -czf "$out/$online" CyberController-Container-Alert

# The offline package adds the prebuilt image and its own checksum manifest.
cp "$image" "$out/watchdog.tar"
cp "$out/watchdog.tar" "$dir/watchdog.tar"
(cd "$out" && sha256sum watchdog.tar) > "$dir/SHA256SUMS"
tar -C "$tmp" -czf "$out/$offline" CyberController-Container-Alert

# Public release checksum file covers every downloadable binary asset.
(cd "$out" && sha256sum watchdog.tar "$online" "$offline") > "$out/SHA256SUMS"
for asset in watchdog.tar "$online" "$offline" SHA256SUMS; do
    if [ "$(stat -c '%s' "$out/$asset")" -ge 2147483648 ]; then
        echo "GitHub release asset exceeds 2 GiB: $asset" >&2
        exit 1
    fi
done
echo "Built release bundles for v$version in $out"
