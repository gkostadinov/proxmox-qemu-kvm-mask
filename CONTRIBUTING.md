# Contributing

Keep recipes explicit and version-pinned. Do not add silent fallbacks, patch fuzz, forced module loading, identity regeneration or automatic host upgrades.

Run `python3 -m unittest discover -v`. Add failure-path tests for deployment changes. New kernel/platform recipes need ABI evidence, disposable guest checks, installation/rollback and a host reboot on a test host. Summarize the tested scope and remaining limits accurately.

Use synthetic test inventories. Do not include personal operational data, keys or binaries. Contributions to existing patched files must retain their applicable upstream license.
