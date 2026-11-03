# Kernel security upgrades

The kernel `7.0.14-26-pve` recipe supports rebuilding the custom KVM core against a Proxmox vendor kernel containing the CVE-2026-98432 fix. Proxmox first included the backport in package `7.0.14-25`. The older `7.0.14-22-pve` recipe describes an affected kernel and is retained for reproduction.

The upstream-looking base version alone is insufficient to assess a vendor kernel. Proxmox retains the `7.0.14` base while carrying backports. See the [vendor update introducing the fix](https://github.com/proxmox/pve-kernel/commit/52cb2c6eabbdaa8b18a515ca2928a5a2d50c62c7) and [first fixed package](https://github.com/proxmox/pve-kernel/commit/af11302954864678da19aa13b2daa38231b26299).

## What is preserved

A kernel-only migration can retain the existing QEMU binary and package, QEMU patches, OVMF images, VM configuration and device assignments. The custom KVM core must be rebuilt for the new kernel. Required DKMS modules need their own matching builds and trusted signatures. The build command prepares an artifact and ABI evidence. It does not install the kernel, change the boot selection, install the module or reboot.

An existing custom host needs a reviewed manual migration. The full installer's guards for existing custom KVM modules, complete artifacts and transaction ownership still apply. A kernel-only build is not a complete artifact set for `apply`.

## Prepare before reboot

1. Record the running kernel, boot selection, existing QEMU binary digest, custom module paths, VM states and relevant configuration. Keep the old kernel and matching modules as a recovery option. Preserve the existing package hold policy.
2. Install the supported target kernel and its exact matching headers through Proxmox's package repositories. Retain the current boot selection while preparing and reviewing the new modules. Review a package simulation first so an unrelated upgrade does not replace a custom QEMU build.
3. Confirm the exact source pins and all vendor KVM patches in the selected recipe. Kernel 26 uses [Proxmox commit `78f3d3e`](https://github.com/proxmox/pve-kernel/commit/78f3d3e2f3ce28c51b80919bc3fc35a399ddb3c7), [Ubuntu source `49ecb900`](https://git.proxmox.com/?p=mirror_ubuntu-kernels.git;a=commit;h=49ecb900ee95547cf6e887aba22a5ce52c617b78) and 40 relevant vendor patch files. Its local patch adjusts source context while retaining the original hypercall behavior.
4. Prepare and review the target build:

   ```sh
   ./setup.sh build --components kernel --target-kernel 7.0.14-26-pve \
     --work work-kernel-26 --dry-run
   ./setup.sh build --components kernel --target-kernel 7.0.14-26-pve \
     --work work-kernel-26 --execute \
     --signing-key /path/to/local/signing.key \
     --certificate /path/to/enrolled-certificate.der
   ```

   The source cache must contain the newly pinned commits if used. Use a fresh work directory. Do not edit an older prepared source tree or force an old module to load by changing its vermagic.
5. Review the build manifest and ABI result. The rebuilt core's export set and CRCs must exactly match the target headers. All imports and the target kernel's stock Intel/AMD consumer dependencies must pass. Signature metadata must identify the expected local signing certificate.
6. Verify builds for every required DKMS module, including any GPU unlock, USB network or hardware monitoring driver. With Secure Boot enabled, confirm that the signing certificates are enrolled and trusted. Keep signature enforcement enabled.
7. Install the reviewed custom core into the intended override location for the target kernel, regenerate its module dependency index and rebuild its initramfs. Preserve or update the persistence helper that enables `hidden_guest_ud` for the new module. Verify that early boot still loads any GPU unlock before the device's normal driver or VFIO.
8. Gracefully shut down guests for the host reboot, select the reviewed target kernel and reboot normally. Do not unload an active KVM module while guests are running. Restore the intended guest running states after verification.

Private signing keys, operational manifests, VM configuration and local recovery records stay on the operator's machines. They do not belong in a public issue, build log or repository.

## Check the running system

After reboot, confirm all of the following:

- `uname -r` reports the intended fixed kernel. An installed package with an older kernel still running does not complete remediation.
- The intended custom KVM core is selected and loaded, with `hidden_guest_ud=Y` if that behavior is required. Verify both the module path and the live parameter.
- Secure Boot remains in the intended state, and required signed modules actually loaded without signature or symbol errors.
- Required GPU unlock, network and monitoring drivers loaded for the new kernel. Check the unlock's own successful handoff and cleanup evidence.
- The QEMU binary digest and all configuration and firmware intended to remain unchanged match their recorded values.
- Proxmox services, storage and networking are healthy, the intended guests start, and their required services work. Inspect new kernel logs for faults.

The manual migration from kernel 22 to kernel 26 has been checked through a reboot on one Intel host. The active kernel and custom KVM parameter were confirmed, Secure Boot remained enabled, the existing QEMU binary was unchanged, and the GPU unlock, USB network and monitoring modules loaded. Proxmox services and NAS storage/SMB checks passed, and VM configuration files were unchanged.

That observation applies to that manual migration. It does not establish complete automated installation or rollback coverage, general hardware compatibility, every guest workload or application acceptance. Independent installer testing on a disposable host remains necessary.

## Linux guests need separate fixes

Updating Proxmox does not replace the kernels inside Ubuntu or TrueNAS guests. Containers share the kernel of the system hosting them, so updating an application image alone does not fix that kernel.

As checked on **3 November 2026**:

| System | CVE-2026-98432 status |
| --- | --- |
| Proxmox `7.0.14-26-pve` | Contains the vendor backport. Verify that this kernel is actually running. |
| Ubuntu 26.04 generic kernel | Canonical still marks the fix work in progress in `7.0.0-43.43`. `7.0.0-42.42` is insufficient. |
| TrueNAS 25.10.7 | Affected. The fix is in the 25.10.8 source branch, but that fixed stable release was not yet publicly available. |

Recheck the [Canonical tracker](https://ubuntu.com/security/CVE-2026-98432), [TrueNAS fix](https://github.com/truenas/linux/pull/377) and [TrueNAS Software Status](https://www.truenas.com/docs/softwarestatus/) before acting. Use the supported TrueNAS update workflow rather than installing Debian packages with `apt` on the appliance.

Filtering incoming IPv4 TCP packets carrying IP options can reduce exposure while waiting for a fixed guest kernel. The [vendor mitigation bulletin](https://access.redhat.com/security/vulnerabilities/RHSB-2026-016) explicitly says this does not fully prevent exploitation and is not a substitute for a kernel fix. A mitigation or a clean race test must not be reported as a patched kernel.
