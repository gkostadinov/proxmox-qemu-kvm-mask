#!/bin/sh
# SPDX-License-Identifier: GPL-2.0-only
# Explicit, VM-scoped installation. Does not restart the guest or Proxmox.
set -eu

if [ "$#" -ne 1 ]; then
    echo "usage: $0 VMID" >&2
    exit 2
fi
case "$1" in
    *[!0-9]* | '' | 0*) echo "VMID must be a decimal Proxmox VM ID" >&2; exit 2 ;;
esac
vmid=$1
if [ "$(id -u)" -ne 0 ]; then
    echo "Run as root on the Proxmox host" >&2
    exit 2
fi

src=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
if [ ! -f "/etc/pve/qemu-server/$vmid.conf" ]; then
    echo "VM $vmid is not configured on this node" >&2
    exit 2
fi
perl -c "$src/tools/pve-usb-hotplug"
perl "$src/tools/pve-usb-hotplug" --vmid "$vmid" --check

helper=/usr/local/libexec/pve-usb-hotplug
unit=/etc/systemd/system/pve-usb-hotplug@.service
instance="pve-usb-hotplug@$vmid.service"
backup=$(mktemp -d "${TMPDIR:-/tmp}/pve-usb-hotplug.XXXXXXXX")
had_helper=no
had_unit=no
was_active=no
was_enabled=no
[ ! -e "$helper" ] || { cp -p "$helper" "$backup/helper"; had_helper=yes; }
[ ! -e "$unit" ] || { cp -p "$unit" "$backup/unit"; had_unit=yes; }
systemctl is-active --quiet "$instance" && was_active=yes || :
systemctl is-enabled --quiet "$instance" && was_enabled=yes || :

restore_on_exit() {
    result=$1
    trap - 0
    if [ "$result" -ne 0 ]; then
        restored=yes
        if [ "$had_helper" = yes ]; then
            cp -p "$backup/helper" "$helper" || restored=no
        else
            rm -f "$helper" || restored=no
        fi
        if [ "$had_unit" = yes ]; then
            cp -p "$backup/unit" "$unit" || restored=no
        else
            rm -f "$unit" || restored=no
        fi
        systemctl daemon-reload || restored=no
        if [ "$was_enabled" = yes ]; then
            systemctl enable "$instance" || restored=no
        else
            systemctl disable "$instance" || restored=no
        fi
        if [ "$was_active" = yes ]; then
            systemctl restart "$instance" || restored=no
        else
            systemctl stop "$instance" || restored=no
        fi
        if [ "$restored" = no ]; then
            echo "Installation failed and automatic restoration was incomplete; backups remain at $backup" >&2
            exit "$result"
        fi
        echo "Installation failed; previous helper, unit, and instance state restored" >&2
    fi
    rm -f -- "$backup/helper" "$backup/unit"
    rmdir -- "$backup"
    exit "$result"
}
trap 'restore_on_exit "$?"' 0
trap 'exit 1' 1 2 3 15

install -d -m 0755 /usr/local/libexec
install -m 0755 "$src/tools/pve-usb-hotplug" "$helper"
install -m 0644 "$src/systemd/pve-usb-hotplug@.service" "$unit"
systemd-analyze verify "$unit"
systemctl daemon-reload
systemctl enable "$instance"
systemctl restart "$instance"
systemctl is-active --quiet "$instance"
echo "$instance is active"
