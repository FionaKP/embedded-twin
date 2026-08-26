#!/bin/sh
# Fetch the FreeRTOS kernel (MIT licensed) pinned to a known-good release.
# The kernel/ directory is gitignored; run this once before building.
set -e
cd "$(dirname "$0")"
VERSION=V11.1.0
if [ -d kernel ]; then
    echo "kernel/ already present"
    exit 0
fi
curl -sL "https://github.com/FreeRTOS/FreeRTOS-Kernel/archive/refs/tags/${VERSION}.tar.gz" -o kernel.tar.gz
mkdir kernel
tar -xzf kernel.tar.gz -C kernel --strip-components=1
rm kernel.tar.gz
echo "FreeRTOS kernel ${VERSION} -> $(pwd)/kernel"
