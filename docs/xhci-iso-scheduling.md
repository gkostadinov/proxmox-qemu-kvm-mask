# xHCI isochronous scheduling guard

A delayed explicit-frame isochronous transfer can reconstruct a Frame ID in
the past. Advancing it by an entire MFINDEX epoch can then defer USB audio or
video traffic for roughly two seconds. The patch clamps reconstructed targets
beyond the 895 ms scheduling horizon to the current microframe index.
Schedule Immediately and interrupt-transfer scheduling remain unchanged.

The version-pinned QEMU recipe includes this additive patch and advances its
package revision. Existing board, routing and USB lifetime patches remain in
the recipe. The build exercises the extracted scheduling function before
packaging. CI verifies strict patch application against hash-pinned upstream
source, requires the baseline to fail overdue-frame checks, then requires the
patched source to pass overdue, valid-future and Schedule Immediately cases
across several epochs. These source-level checks do not replace hardware
playback and guest compatibility testing.

Run `python tools/check_xhci_iso_patch.py` on a machine with a C compiler and
`patch` installed. To test an already prepared source tree, run
`python tools/test_xhci_iso_scheduling.py SOURCE/hw/usb/hcd-xhci.c`.

The guard follows the [QEMU developer proposal](https://mid.mail-archive.com/qemu-devel@nongnu.org/msg1228666.html)
by Tong Liang Chew and Dongwon Kim. This is not a claim of upstream acceptance
or a guarantee that unrelated audio dropouts are resolved.
