# Architecture

The framework reduces selected guest-visible virtualization fingerprints by patching QEMU device/ACPI identities, KVM hypercall behavior and OVMF firmware metadata. Detection, build recipes and transaction handling make those patches repeatable on a supported host.

VM argument generation, guest checks and the boot helper are specific to the included Proxmox recipe for marker masking.

The source entry point is `setup.sh`, which forwards to the internal Python package `vmpkit`. The installed public command is `qemu-mask`. The Python package uses only the standard library. Build programs come from the host's separately installed toolchain.

| Module | Responsibility |
| --- | --- |
| `detect.py` | Read-only inventory and allowlist-based redaction |
| `plan.py` | Exact recipe selection, editable non-serial identity and VM argument generation |
| `build.py` | Pinned checkouts, strict patches, matching builds, ABI checks and local signing |
| `verify.py` | Disposable Linux boots using only scratch disks and isolated networking |
| `transaction.py` | Reviewed-plan guards, graceful downtime, durable journals and rollback |
| `cli.py` | Explicit command boundaries and dry-run defaults |

## Initial recipe

`recipes/pve9-intel-7.0.14-22.json` pins Proxmox QEMU, kernel and firmware repositories and their upstream submodules. It accepts the tested QEMU base versions 11.0.3-4 and 11.1.1-2, builds 11.1.1-2+vmp1 and requires the exact running kernel and firmware package. Local QEMU suffixes may be detected for planning, but installation additionally requires a stock KVM core and an exact rollback package.

The QEMU base patch is downloaded from a pinned AICodo release and verified by SHA-256. The unused HPPA firmware hunk is excluded, matching the selected x86 build. QEMU's upstream patch is followed by the local board and routing patches. The board option is disabled by default; VFIO devices are excluded from identity rewriting.

Routing NameSeg changes apply at the encoder, covering declarations and references together. Existing package lengths, IRQ values and resources are retained. MADT interrupt-source overrides and CPU timing behavior are not changed.

KVM preparation copies only the relevant source directories and applies the 37 matching Proxmox patches affecting those directories. A small optional patch injects #UD for VM/VMMCALL when the guest CPUID hypervisor bit is clear. Advertised guests retain the control behavior. Stock Intel/AMD consumer ABI checks are required; only Intel runtime deployment is supported.

OVMF is built using matching Proxmox SMM, Secure Boot, TPM and 4 MiB split-flash settings. Vendor/date/version metadata comes from the reviewed identity. Only CODE is deployed; existing variables and TPM volumes stay selected.

## Identity and supported VM shape

Initial target requirements: Windows, q35 10.1, 4 MiB OVMF, host CPU, SATA storage and an existing valid SMBIOS UUID. Serial numbers are never detected or inserted. An existing type-1 SMBIOS override is blocked for manual UUID review. Existing type-0/2/3/4 arguments are replaced through parsed argument lists rather than shell substitution.

Hardware metadata supplies defaults; it cannot establish device-driver compatibility on an unknown platform. Adding a recipe requires reviewed source pins, strict patch application, ABI checks, guest tests, installation/rollback and a host reboot test. Automatic patch fuzz, vermagic editing, forced module loading and guessed vendor mappings are not supported.
