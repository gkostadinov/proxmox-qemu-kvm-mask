# USB passthrough lifetime fix

`patches/qemu-usb-lifetime.patch` addresses QEMU USB host transfer cleanup when a physical device disappears or a hub is switched. It applies to the recipe's pinned QEMU source and is included in package version `11.1.1-2+vmp2`.

## Changes

- Free the request immediately when control, bulk or interrupt transfer submission fails. No asynchronous completion callback follows a failed submission.
- Clear the request's packet pointer and start cancellation before notifying the guest controller. Completion may reenter cancellation; it must not observe the packet as still pending.
- Delete the no-device and post-load bottom halves after device close drains pending callbacks. Clear their associated state.

The failed-submission lifetime problem is described in upstream [issue 3595](https://gitlab.com/qemu-project/qemu/-/work_items/3595). Upstream [issue 4496](https://gitlab.com/qemu-project/qemu/-/work_items/4496) reports a closely matching abort crash on QEMU 11.1.1. These reports provide context; they are not upstream approval of this local patch.

## Validation

Run:

```sh
python3 tools/check_usb_lifetime_patch.py
```

This downloads only the exact upstream `host-libusb.c` at the pinned commit, verifies its SHA-256, requires the original source to fail ordinary ownership/order assertions, applies the patch with zero fuzz, and requires the patched source to pass. A C compiler and `patch` are required.

The contract harness executes four actual source functions with mocked dependencies. It covers failed and successful control/bulk/interrupt submissions, repeated abort, completion callback reentry, and deferred callback cleanup after draining. The original source fails 23 of 81 assertions; the patch passes all 81.

The same patch has also compiled for x86_64 and aarch64, passed 13 USB controller qtests, and completed 200 unmatched USB-host add/remove cycles on each of the QEMU and NEC xHCI controllers. Unmatched selectors avoid claiming physical devices; those cycles exercise object creation/deletion, not active physical transfers.

Physical hub switching in a running guest has not yet been verified with the patched process. Test that separately on a disposable guest before relying on it for valuable work. A full VM shutdown/start is needed to replace its QEMU process; a guest OS reboot alone retains the old process.

## Device ownership

Assign a physical USB device to only one running VM at a time. Overlapping port selectors can cause competing claim/reset attempts. This patch does not change VM assignments or implement USB sharing.
