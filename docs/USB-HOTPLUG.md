# Host USB hotplug for Proxmox `other` OS guests

Proxmox's `usbN` add/remove path applies changes live only for machine version 7.1 or newer with Linux `l26` or Windows newer than 7 as the configured OS type. A guest configured as `ostype: other` leaves these changes pending until a VM stop/start, even if USB is in the `hotplug` feature list. This is a Proxmox configuration rule. QEMU can add and remove a `usb-host` device on an xHCI controller that the guest already sees.

`tools/pve-usb-hotplug` bridges that gap for **one explicitly selected VM**. It watches that VM's pending USB settings, changes its running QEMU device through QMP, verifies removal or addition, then updates only the successful USB setting under Proxmox's native configuration lock. It does not change the VM's OS type, patch `qemu-server`, add PCI controllers to a running guest, or touch other VMs. It is independent of this repository's [USB lifetime patch](USB-LIFETIME.md), which addresses QEMU transfer cleanup when physical devices disappear.

## Support and limits

- Tested live with `qemu-server` 9.2.10, Proxmox 9.2 and a patched QEMU 11.1.1 build on an x86_64 macOS guest. The helper itself does not depend on that QEMU patch, but stock-build runtime behavior has not been verified. The code uses Proxmox Perl APIs; review and retest it after a `qemu-server` upgrade. This is not a claim of validation across every Proxmox version or guest OS.
- The target must use `ostype: other`, a machine version at least 7.1, USB hotplug enabled, a stable SMBIOS UUID or VM generation ID, and an xHCI controller **already visible in the running guest**. A custom always-present xHCI controller is useful when all `usbN` assignments may be removed: Proxmox otherwise omits its auto-created xHCI controller at the next cold start if no USB3 assignment remains.
- Supports the five native `usb0` through `usb4` slots available to `ostype: other` in this Proxmox version. Host-port and vendor/product selectors, including Proxmox USB mappings, are supported. SPICE USB redirection and PCI controller passthrough are outside this helper.
- The helper selects existing NEC or QEMU xHCI buses, gives each device an explicit free guest port, and leaves the controllers present when the final USB device is removed. If none has a free port, the setting remains pending with a logged error. It never silently creates a new USB hub or PCI controller.
- Successful assignments are stored with `usb3=1`, even for a USB2 peripheral: in this Proxmox configuration that flag selects the xHCI **guest controller**. Keeping `usb3=0` would route the device to EHCI/UHCI after the next cold start. The actual peripheral speed is negotiated normally.

For an always-present controller, review the VM's existing arguments and add a device such as `-device qemu-xhci,id=usbhotplug,p2=7,p3=7` to the VM's startup arguments. Do not replace existing arguments wholesale. A new PCI controller requires the next planned full VM stop/start before the guest can use it. The helper can then apply subsequent USB changes live. Seven USB attachment ports permit two virtual input devices plus five `usbN` devices; `p2=7,p3=7` exposes fourteen root ports. If Proxmox also creates a NEC controller for cold-start USB assignments, size that controller for the expected count too. Check the guest's own USB port mapping limits before increasing controller ports.

## Install for a chosen VM

Run on the Proxmox host as root from this checkout while the target VM is running, substituting your own VM ID:

```sh
perl tools/pve-usb-hotplug --vmid 200 --check
tools/install-pve-usb-hotplug.sh 200
systemctl status pve-usb-hotplug@200.service
```

The preflight checks Proxmox configuration, running machine version and host QMP xHCI controllers. It cannot prove the guest enumerates those controllers; check that in the guest before relying on hotplug. The installer checks Perl syntax and the systemd unit, then installs the helper under `/usr/local/libexec/` and enables a VM-specific service. If installation or service startup fails, it restores the previous helper, unit and selected instance state. It does not restart the guest, Proxmox services, or other helper instances. If installing an updated script for several VMs, restart each affected helper instance so it loads the new code.

Service state lives in `/var/lib/pve-usb-hotplug/VMID/`, root-only. On first use it pins a SHA-256 digest of the VM's existing SMBIOS UUID or generation ID; the identity value itself is never placed in the repository or logs. If the VM identity later changes through replacement or restore, the helper refuses to apply USB changes until an administrator checks the target and deliberately resets that local pin. Before each attempted change, it keeps a private configuration backup (last 100 retained).

Use the normal Proxmox **VM → Hardware → Add → USB Device**, edit, or remove flow. While the VM is running, the helper normally applies a pending change within a few seconds. If the guest does not see a newly attached physical device, inspect the host's QMP result and the guest USB tree separately. A QEMU attachment alone does not establish guest detection or application behavior.

Check the service and pending settings:

```sh
systemctl is-active pve-usb-hotplug@200.service
journalctl -u pve-usb-hotplug@200.service -n 40 --no-pager
qm pending 200
```

An occupied-port probe can be logged by QMP immediately before the helper succeeds on the next port. An actual failure leaves the USB setting pending and logs `ERROR`. The helper attempts to restore a replaced device if the new attachment fails. If a config write fails after a successful QMP change, its next pass compares the live selector and completes the config write without reattaching the device; inspect both QMP and `qm pending` if errors continue. Other pending hardware changes are left alone. It also respects an active Proxmox VM lifecycle lock and a disabled USB hotplug feature.

## Disable and remove

```sh
systemctl disable --now pve-usb-hotplug@200.service
```

This leaves currently attached devices alone. Future `usbN` edits again follow Proxmox's stock pending-until-stop/start behavior. Review any current pending edits before removing private state. To uninstall the helper entirely, disable all instances, then remove `/usr/local/libexec/pve-usb-hotplug` and `/etc/systemd/system/pve-usb-hotplug@.service` and reload systemd. Keep private backups only as long as they are needed; never commit them.
