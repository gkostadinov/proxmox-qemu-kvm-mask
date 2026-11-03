# Building

Run builds on the matched Debian/Proxmox platform with Python 3.11+, Git, patch, quilt, make, GCC 14, dpkg-dev and the exact headers for the selected kernel. The current recipe needs `proxmox-headers-7.0.14-26-pve`. The historical kernel 22 recipe needs `proxmox-headers-7.0.14-22-pve`. Build compilation does not need installation authority; an offline private inventory can be captured by an administrator for a build account.

QEMU prerequisites are defined by `debian/control` in the pinned Proxmox QEMU checkout. `dpkg-checkbuilddeps` checks them before compilation. They include the Proxmox backup development library, GLib, pixman, USB, SPICE, Ceph, networking, audio, Meson and documentation libraries. Install reviewed dependencies separately; the toolkit never invokes `apt install`.

OVMF requires `nasm`, `iasl`, `uuid-dev`, `gcc-multilib`, `g++-multilib` and the pinned firmware submodules. Disposable guests require `busybox`, `libc6-dev` and static-capable GCC. Local signing uses `mokutil` and the matching header tree's `scripts/sign-file`. The original blank splash asset needs no image-library dependency.

Use a fresh work directory on a filesystem with at least 35 GiB available and no whitespace in its absolute path. Eight build jobs are the default; reduce them on smaller hosts. Build failures retain prepared sources for troubleshooting. A repeated component build refuses an existing prepared tree; use a new work directory for a fresh build.

## Source preparation

Git checkouts are detached at pinned commits. Nested EDK II submodules use the gitlinks from the pinned upstream commit. Proxmox's packaging and firmware patches are applied before local changes. `patch --fuzz=0` is required. QEMU wrap dependencies retain their upstream checksum definitions.

QEMU preparation also compiles and runs the USB lifetime contract harness against the actual patched source. A failed check stops the build; its output is saved as `work/usb-lifetime-contract.txt`. GCC or another C compiler must be available. For the independent upstream/baseline check, run `python3 tools/check_usb_lifetime_patch.py`; this downloads one hash-pinned source file and uses only a temporary directory.

The optional `--source-cache` accepts local repositories arranged as `pve-qemu`, `pve-kernel` and `pve-edk2-firmware`, with the matching submodule commits available. It avoids repeated downloads without modifying the cached repositories.

## Kernel 26 source recipe

The `7.0.14-26-pve` recipe pins the following vendor sources:

| Input | Pinned value |
| --- | --- |
| Proxmox kernel repository | `78f3d3e2f3ce28c51b80919bc3fc35a399ddb3c7` |
| Ubuntu kernel submodule | `49ecb900ee95547cf6e887aba22a5ce52c617b78` |
| Proxmox patch files touching `arch/x86/kvm` or `virt/kvm` | 40 |
| Local KVM patch | `kvm-hypercall-7.0.14-26.patch` |

The build copies the matching KVM sources, applies the relevant blocks from all 40 vendor patch files in filename order, and then applies the local hypercall patch. Header changes from the same vendor build come from the exact matching kernel header package. The older kernel 22 recipe selects 37 vendor patch files. Reusing that old prepared source would lose newer vendor changes.

The kernel 26 hypercall patch ports the original patch's context around newly added nested-ops declarations. Its added and deleted code is identical to the original patch. The opt-in parameter and hidden versus advertised guest behavior are unchanged. Both patches are retained because their vendor source contexts differ.

To prepare only a replacement KVM core for an installed target kernel before rebooting, use `build --components kernel --target-kernel 7.0.14-26-pve`. This selects a matching recipe and target headers without replacing the running kernel, QEMU or firmware. See [the manual upgrade workflow](KERNEL-UPGRADE.md). Complete installation artifacts and existing apply guards are still required by the full installer.

## ABI and signing

The rebuilt core's vermagic must match the selected kernel. Every KVM export and CRC must equal that kernel's installed header `Module.symvers`; imports and stock consumer dependencies are checked too. A build targeting a kernel that is not yet running must resolve its stock consumer modules using that target kernel, rather than inspecting consumers from the current boot. The recipe does not replace the running kernel or the stock Intel/AMD consumers.

Signing passes the private key path directly to the local kernel signing tool. Private key bytes are neither read by the Python wrapper nor included in manifests. An already-enrolled DER certificate is required, and key permissions are checked. Certificate enrollment remains a manual host-administration operation.

For an upgrade, verify that every required DKMS module also builds for the target kernel and that its signing certificate is trusted when Secure Boot is enabled. Rebuild the target initramfs after installing the reviewed KVM core. A readable module signer alone does not establish certificate trust or successful loading.

Build manifests contain recipe/patch digests, artifact hashes, identity metadata, ABI counts and signing status. They do not contain the private key path or private key contents. They are local runtime data and are not intended for commit.

## Reproducibility boundary

Inputs and source commits are pinned. Packaging and firmware use a fixed source epoch, and the KVM compiler maps work paths to a generic source prefix. Identity parameters and the signing certificate vary by machine. Byte-for-byte identical outputs across different toolchains and certificates have not been established; record build environment versions when publishing binaries.

Binary distributions need complete corresponding source, upstream license files and component notices alongside the source provenance.
