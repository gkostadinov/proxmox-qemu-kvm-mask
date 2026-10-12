# SPDX-License-Identifier: GPL-2.0-only
<# Read-only postboot checks. Output contains local UUID/BIOS metadata; keep it private. #>
param([Parameter(Mandatory=$true)][Guid]$ExpectedUUID)
$ErrorActionPreference = 'Stop'
if (-not ('VirtualPlatformCodeIntegrity' -as [type])) {
    Add-Type @'
using System;
using System.Runtime.InteropServices;
public static class VirtualPlatformCodeIntegrity {
    [StructLayout(LayoutKind.Sequential)]
    struct Info { public uint Length; public uint Options; }
    [DllImport("ntdll.dll")]
    static extern int NtQuerySystemInformation(int kind, ref Info value, uint size, out uint returned);
    public static uint Read() {
        var value = new Info { Length = 8 };
        uint returned;
        int status = NtQuerySystemInformation(103, ref value, 8, out returned);
        if (status < 0) throw new InvalidOperationException("Code Integrity query failed");
        return value.Options;
    }
}
'@
}
$Integrity = [VirtualPlatformCodeIntegrity]::Read()
$ActualUUID = [Guid](Get-CimInstance Win32_ComputerSystemProduct).UUID
$BIOS = Get-CimInstance Win32_BIOS
$Result = [ordered]@{
    RecordedUtc = [DateTime]::UtcNow.ToString('o')
    UUIDPreserved = ($ActualUUID -eq $ExpectedUUID)
    UUID = $ActualUUID.ToString()
    CodeIntegrityEnabled = (($Integrity -band 1) -ne 0)
    TestSigningActive = (($Integrity -band 2) -ne 0)
    CodeIntegrityOptions = ('0x{0:X}' -f $Integrity)
    BIOSVendor = $BIOS.Manufacturer
    BIOSVersion = $BIOS.BIOSVersion
    Video = @(Get-CimInstance Win32_VideoController | Select-Object Name, Status, ConfigManagerErrorCode)
}
$Result | ConvertTo-Json -Depth 4
if (-not $Result.UUIDPreserved -or -not $Result.CodeIntegrityEnabled -or $Result.TestSigningActive) {
    throw 'Postboot UUID or normal signature-enforcement checks failed.'
}
