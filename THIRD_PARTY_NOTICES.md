# Third-party notices

Original build orchestration and tests are GPL-2.0-only; see `LICENSE`. Small patches retain the licenses of the files they modify. Upstream source trees, firmware artwork, packages, kernel modules, signing keys and operational evidence are not bundled.

| Component | Source and license |
| --- | --- |
| Proxmox QEMU packaging | https://git.proxmox.com/git/pve-qemu.git — preserve its source/package notices |
| QEMU | https://www.qemu.org/docs/master/about/license.html — GPLv2 overall, with file-specific compatible licenses |
| Linux/KVM | https://docs.kernel.org/process/license-rules.html — GPL-2.0-only overall, with file-specific notices |
| Proxmox kernel packaging | https://git.proxmox.com/git/pve-kernel.git — preserve its source/package notices |
| EDK II / OVMF | https://github.com/tianocore/edk2/blob/master/License.txt — primarily BSD-2-Clause-Patent, with additional component licenses |
| Proxmox firmware packaging | https://git.proxmox.com/git/pve-edk2-firmware.git — preserve its source/package notices |
| Base QEMU patch | https://github.com/AICodo/pve-emu-realpc — credited source of the SHA-256-pinned QEMU modifications; file-level upstream licenses remain applicable |

Firmware modifications are generated against the local EDK II source and retain its applicable license. Preserve nested dependency licenses when distributing complete source or binaries. Review the exact downloaded sources and notices for a binary release; this list is an attribution map rather than a substitute for their license texts.
