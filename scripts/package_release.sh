#!/usr/bin/env bash
# Build one customer package from an already-built release image.
# No Docker commands; safe to test with a disposable fake archive.
# Usage: bash scripts/package_release.sh <watchdog-image.tar> <output-directory>
set -euo pipefail

if [ "$#" -ne 2 ]; then
    echo "Usage: $0 <watchdog-image.tar> <output-directory>" >&2
    exit 2
fi

repo="$(cd "$(dirname "$0")/.." && pwd)"
version="$(tr -d '[:space:]' < "$repo/VERSION")"
if ! [[ "$version" =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]]; then
    echo "Invalid VERSION: $version" >&2
    exit 1
fi

image="$(realpath "$1")"
if [ ! -s "$image" ]; then
    echo "Prebuilt image archive is missing or empty" >&2
    exit 1
fi

mkdir -p "$2"
out="$(cd "$2" && pwd)"
work="$(mktemp -d)"
trap 'rm -rf "$work"' EXIT

dir="$work/CyberController-Container-Alert"
mkdir -p "$dir/docs/customer"
for f in install.sh uninstall.sh docker-compose.yaml VERSION \
         .env.example watchdog-config.yaml.example README.md; do
    cp "$repo/$f" "$dir/$f"
done
cp "$repo"/docs/customer/*.md "$dir/docs/customer/"  # only customer guides ship

cp "$image" "$dir/watchdog.tar"
(cd "$dir" && sha256sum watchdog.tar > SHA256SUMS)

package="container-alert-v$version.tar.gz"
tar -C "$work" -czf "$out/$package" CyberController-Container-Alert
(cd "$out" && sha256sum "$package" > SHA256SUMS)

for asset in "$package" SHA256SUMS; do
    if [ "$(stat -c '%s' "$out/$asset")" -ge 2147483648 ]; then
        echo "Release asset exceeds the 2 GiB GitHub limit: $asset" >&2
        exit 1
    fi
done
echo "Packaged Container Alert v$version: $out/$package"
