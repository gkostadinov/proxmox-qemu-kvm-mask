#!/bin/sh
# SPDX-License-Identifier: GPL-2.0-only
# This entry point forwards arguments. Running it never installs dependencies.
set -eu
cd "$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)"
exec python3 -m vmpkit "$@"
