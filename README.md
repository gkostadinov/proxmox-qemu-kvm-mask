# Proxmox QEMU/KVM Mask

**Marker masking for QEMU, KVM and OVMF on Proxmox.** It builds and deploys patches that reduce selected guest-visible fingerprints used to detect virtual machines: device identities, ACPI signatures, firmware branding, CPUID advertising and hypercall behavior.

System detection, version-matched builds, dry-run, validation and rollback support that patching workflow. Hardware identity comes from reviewed host metadata, and the VM's existing UUID is preserved.

The planner, guest checks and KVM helper target the included Proxmox recipe for marker masking.

## Markers it changes

| Layer | Fingerprints addressed by the initial recipe |
| --- | --- |
| **QEMU devices** | Selected QEMU/Red Hat PCI bridge and subsystem IDs, emulated device descriptions and the xHCI controller identity. The local board patch excludes VFIO passthrough devices. |
| **QEMU ACPI** | Synthetic resource IDs such as `PNP0A06`, resource descriptions and routing names such as `PRTP` / `PRTA`. Declarations and references are renamed together while retaining resources and IRQ values. |
| **Guest CPU / SMBIOS** | Hypervisor/KVM CPUID advertising and BIOS/board/CPU identity fields, using generated VM arguments and an editable identity. The existing SMBIOS UUID stays unchanged. |
| **KVM** | An optional core patch injects `#UD` for VM/VMMCALL when the guest's CPUID hypervisor bit is clear. Advertised guests retain the control behavior. |
| **OVMF firmware** | Underlying firmware vendor, version and date strings, using a matching custom CODE image and the existing guest variable store. |

Coverage is selective. Virtual GPU, timing, debug-trap, measured-boot and some MADT/firmware heuristics can remain detectable. Application and anti-cheat acceptance require separate testing and can change with updates.

## Supported starting point

The included recipe targets **Proxmox 9 on Intel x86_64, kernel 7.0.14-22-pve, QEMU 11.1.1 and OVMF 4.2026.08**. Unknown combinations stop before installation. Source commits, patch hashes, kernel symbol compatibility and module signatures are checked explicitly.

QEMU builds include [USB passthrough lifetime fixes](docs/USB-LIFETIME.md) for failed transfer submission and device removal. The recipe produces `11.1.1-2+vmp2` and checks the patched USB error paths before compilation.

For guests configured as Proxmox OS type `other`, a separate [host USB hotplug helper](docs/USB-HOTPLUG.md) can apply USB changes from the normal Hardware panel without a VM restart. It uses an existing xHCI controller and does not modify the QEMU/KVM masking build or Proxmox packages. It has been tested on Proxmox 9.2 with a running macOS guest; other guests need their own runtime verification.

**The masking build and installer are experimental.** Automated tests cover planning, artifact integrity and simulated rollback. End-to-end installation, recovery and host reboot behavior are not yet verified on a disposable Proxmox host. Evaluate that installer on a test host before using it with valuable guests. The independent USB hotplug helper has its own [support limits and validation](docs/USB-HOTPLUG.md).

## Build and installation workflow

- Reads versions, CPU capabilities, firmware metadata and selected VM settings.
- Derives an editable identity from non-serial host metadata.
- Builds matching QEMU, an optional KVM core and VM-specific OVMF CODE.
- Keeps the existing VM UUID, devices, disks, EFI variables and TPM state.
- Uses local module signing; it never exports a private key or disables signature enforcement.
- Defaults build, deployment and rollback to dry-run. Explicit execution is required.
- Records installation intent and completed steps in a root-only transaction journal.
- Restores the exact previous QEMU package, hold policy, VM arguments, stock KVM, initramfs and original running guest set.

QEMU is a shared host package and KVM is a shared kernel module. Applying this profile requires graceful downtime for **all running VMs**. The firmware CODE selection is limited to the chosen VM. Stopped VMs stay stopped. Running containers, existing custom KVM modules and pre-existing custom firmware profiles require manual migration in this release.

## Inspect locally

Python 3.11 or newer is sufficient for detection, offline planning and tests. No Python packages are installed by the entry point.

```sh
./setup.sh --help
./setup.sh plan --snapshot examples/inventory.synthetic.json --vmid 200
./setup.sh build --snapshot examples/inventory.synthetic.json --dry-run
python3 -m unittest discover -v
```

The example is synthetic. It contains no operational host information.

On a supported Proxmox host:

```sh
./setup.sh detect
./setup.sh detect --identity-only --output identity.local.json
./setup.sh plan --vmid 200 --identity identity.local.json --output plan.local.json
```

Choose your actual VM ID. Review the generated identity and plan. Local plans contain VM UUIDs and configuration; they are private runtime files and are ignored by Git. Default detection output is redacted. `detect --private` is available for offline planning but its output must remain private.

## Build and validate

Read [build prerequisites](docs/BUILDING.md) first. The tool never installs build dependencies, upgrades packages, enrolls certificates or reboots the host automatically.

```sh
./setup.sh build --work work --identity identity.local.json --dry-run
./setup.sh build --work work --identity identity.local.json --execute \
  --signing-key /path/to/local/signing.key --certificate /path/to/enrolled-certificate.der
./setup.sh verify --artifacts work/artifacts --output validation.local.json
```

Both signing paths refer to local files. The key must be inaccessible to group and other users, and the DER certificate must already be enrolled. Build output stays in `work/`. A build never installs its artifacts. An original plain black splash is included; no upstream artwork is bundled.

Verification boots three disposable Linux guests: hidden CPU, advertised CPU, and advertised CPU with the board profile disabled. They use no production disks, passthrough devices or network. Checks cover firmware boot, PCI/controller identities, ACPI lengths/checksums, routing names and guest CPUID. The installer additionally checks hypercall behavior after loading the signed module, before restarting production guests.

## Review deployment and rollback

```sh
./setup.sh apply --plan plan.local.json --artifacts work/artifacts \
  --validation validation.local.json --dry-run
```

Inspect the dry-run and plan digest. After separate testing on a disposable host, execution requires root, `--execute`, `--allow-experimental` and `--approve` with that exact digest. If host versions, guest running states, the selected VM configuration or package hold state changed, generate a new plan. An exact-version rollback package must be available; provide `--rollback-package` for a locally built or otherwise unavailable package.

```sh
./setup.sh rollback --transaction /var/lib/vmpkit/transaction-EXAMPLE --dry-run
```

Rollback also defaults to dry-run. Add `--execute` to perform it. Read [transaction behavior and recovery](docs/TRANSACTIONS.md). Modified VM arguments, a changed UUID, a different running kernel or a changed backup package stop restoration for review. EFI variables and TPM state are never restored blindly.

## Documentation

- [Architecture and recipe boundaries](docs/ARCHITECTURE.md)
- [Build prerequisites and source provenance](docs/BUILDING.md)
- [Transactions and rollback](docs/TRANSACTIONS.md)
- [USB passthrough lifetime fix](docs/USB-LIFETIME.md)
- [USB hotplug for `other` OS guests](docs/USB-HOTPLUG.md)
- [Third-party notices](THIRD_PARTY_NOTICES.md)

The original tooling is licensed under GPL-2.0-only. Upstream components retain their own licenses and notices. This repository contains small QEMU/KVM patches and build orchestration; upstream source trees and binaries are not vendored.
