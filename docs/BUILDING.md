# Building

Run builds on the matched Debian/Proxmox platform with Python 3.11+, Git, patch, quilt, make, GCC 14, dpkg-dev and the exact `proxmox-headers-7.0.14-22-pve` headers. Build compilation does not need installation authority; an offline private inventory can be captured by an administrator for a build account.

QEMU prerequisites are defined by `debian/control` in the pinned Proxmox QEMU checkout. `dpkg-checkbuilddeps` checks them before compilation. They include the Proxmox backup development library, GLib, pixman, USB, SPICE, Ceph, networking, audio, Meson and documentation libraries. Install reviewed dependencies separately; the toolkit never invokes `apt install`.

OVMF requires `nasm`, `iasl`, `uuid-dev`, `gcc-multilib`, `g++-multilib` and the pinned firmware submodules. Disposable guests require `busybox`, `libc6-dev` and static-capable GCC. Local signing uses `mokutil` and the matching header tree's `scripts/sign-file`. The original blank splash asset needs no image-library dependency.

Use a fresh work directory on a filesystem with at least 35 GiB available and no whitespace in its absolute path. Eight build jobs are the default; reduce them on smaller hosts. Build failures retain prepared sources for troubleshooting. A repeated component build refuses an existing prepared tree; use a new work directory for a fresh build.

## Source preparation

Git checkouts are detached at pinned commits. Nested EDK II submodules use the gitlinks from the pinned upstream commit. Proxmox's packaging and firmware patches are applied before local changes. `patch --fuzz=0` is required. QEMU wrap dependencies retain their upstream checksum definitions.

The optional `--source-cache` accepts local repositories arranged as `pve-qemu`, `pve-kernel` and `pve-edk2-firmware`, with the matching submodule commits available. It avoids repeated downloads without modifying the cached repositories.

## ABI and signing

The rebuilt core's vermagic must match the running kernel. Every KVM export and CRC must equal the installed header `Module.symvers`; imports and stock consumer dependencies are checked too. The recipe does not replace the running kernel or patched Intel/AMD consumers.

Signing passes the private key path directly to the local kernel signing tool. Private key bytes are neither read by the Python wrapper nor included in manifests. An already-enrolled DER certificate is required, and key permissions are checked. Certificate enrollment remains a manual host-administration operation.

Build manifests contain recipe/patch digests, artifact hashes, identity metadata, ABI counts and signing status. They do not contain the private key path or private key contents. They are local runtime data and are not intended for commit.

## Reproducibility boundary

Inputs and source commits are pinned. Packaging and firmware use a fixed source epoch, and the KVM compiler maps work paths to a generic source prefix. Identity parameters and the signing certificate vary by machine. Byte-for-byte identical outputs across different toolchains and certificates have not been established; record build environment versions when publishing binaries.

Binary distributions need complete corresponding source, upstream license files and component notices alongside the source provenance.
