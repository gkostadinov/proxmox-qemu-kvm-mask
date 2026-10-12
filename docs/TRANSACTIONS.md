# Transactions and recovery

`apply` and `rollback` are dry-runs unless `--execute` is supplied. Apply execution requires root, explicit experimental opt-in, the reviewed plan digest, exact artifact hashes, successful isolated verification and unchanged live host/VM state. The first release refuses pre-existing custom KVM modules, custom CODE profiles and running containers.

The root-owned state directory is mode 0700. A global file lock serializes toolkit transactions. Private plans, VM configuration backups, package backups and intent journals are stored there. Files are written atomically and journals are flushed before each mutation.

## Apply order

1. Obtain and verify the exact previously installed QEMU package for rollback. Back up VM configurations and the current initramfs.
2. Record and gracefully stop the original running VM set. Refuse forced poweroff, a changed guest inventory or any guest left running.
3. Unload the stock Intel consumer/core without force. Install the signed exact-ABI core in a separate updates directory and reload it.
4. Boot isolated hidden and advertised Linux guests. Require SIGILL for both hidden hypercalls and unchanged advertised control behavior.
5. Install the matching QEMU package and hold it. Copy CODE into a VM-specific directory and update only the target's `args`.
6. Verify the UUID and every unowned configuration field, including other VMs. Enable a conditional boot helper and update the current initramfs.
7. Restart only the original running VM set. Mark the transaction as awaiting guest verification.

A failed attempt triggers restoration using the journal. If restoration cannot safely finish, the journal is retained and the tool reports that manual attention is required. Another administrator starting guests during the operation can block module unload; it is never forced.

The helper enables the KVM option only on the recorded kernel. A future kernel uses its own module tree; it requires a new reviewed recipe. The toolkit does not change the host's boot default or install a kernel.

## Rollback

Inspect `rollback --dry-run` first. Execute using the transaction's state directory. The exact backed-up package must still match its recorded hash, and the host must be running the transaction kernel.

Rollback refuses externally edited VM arguments or a changed UUID before taking downtime. It restores only owned VM arguments, the exact QEMU package and previous hold policy, stock KVM module resolution and the backed-up initramfs. It removes its own CODE/helper files and restarts the recorded original running set. Repeating rollback after a completed restoration is harmless.

EFI variables and TPM volumes are not written by this installer, and rollback does not overwrite them. Configuration/initramfs/package backups are not a complete VM disk backup. Protect valuable guests using your usual backup system before evaluating experimental host changes.

## Supported operations

Existing custom profiles and upgrades between toolkit releases are not migrated automatically. Multi-node cluster scheduling and HA-managed guests are unsupported; resolve external lifecycle management before evaluation.
