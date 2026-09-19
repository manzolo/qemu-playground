# Windows 11 profile

[← Project overview](../README.md) · [Usage](USAGE.md) · [Configuration](CONFIGURATION.md) · [CLI](CLI.md)

This profile requires a Windows license appropriate to your use and installation
media matching the pinned **Italian x64** vendor checksum. The answer file selects
`Windows 11 Pro`; it does not activate Windows or bypass licensing/hardware checks.

1. Download the matching ISO from [Microsoft](https://www.microsoft.com/en-us/software-download/windows11).
2. Run `./lab config init` and inspect `.env`.
3. Obtain an x64 QEMU guest agent MSI from a trusted distributor. The
   [Fedora virtio-win project](https://github.com/virtio-win/virtio-win-pkg-scripts/blob/master/README.md)
   documents its distribution channels; `guest-agent/qemu-ga-x86_64.msi` inside
   `virtio-win-<version>.iso` is the usual source. Record where the file came from
   and the digest you computed from that copy, and say so in `LAB_QGA_SOURCE`.
   **These installers are not Authenticode-signed** (verified for 0.1.285 on
   2026-09-16), so the pinned SHA-256 is the trust anchor: the host verifies it
   before staging, and the guest re-verifies it before running msiexec. Signature
   status is reported on COM1 for the record.
4. Set `LAB_QGA_MSI`, `LAB_QGA_SHA256` and `LAB_QGA_SOURCE` in `.env`.
5. Run `./lab doctor`. Install `swtpm` and `ovmf` if missing. The default firmware
   paths use the matching 4 MiB Microsoft-key-enrolled OVMF code/variables pair.
6. Run `./lab iso windows-11 import --source /path/to/vendor.iso`.
7. Run `./lab prepare windows-11`, then `./lab install windows-11`.

Missing ISO, checksum provenance, agent package or firmware blocks preparation.
Windows needs at least 2 vCPUs, 4096 MiB RAM and a 64 GiB disk. The software TPM
state and UEFI variable store are private to the VM and persist across boots.

The seed creates a local administrative lab user, logs on once to bootstrap
OpenSSH and QGA, disables password SSH authentication, removes autologon secrets,
flushes the filesystem cache, emits a completion token, then powers off. Host-side
seed files contain the local password and are private/ignored; clean the seed
when you no longer need it. Network access to Windows Features on Demand is
required for the OpenSSH capability. Failures are reported via COM1, or via the
host timeout and screenshot if Windows Setup never reaches the bootstrap script.

SATA and e1000e avoid a dependency on virtio storage/network drivers during Setup.
QGA uses `isa-serial` on COM2, supported by QEMU's Windows channel implementation;
no virtio serial driver ISO is needed. This is a learning configuration, not a
performance recommendation.

Preparation extracts the vendor ISO and creates UEFI installation media using
its **efisys_noprompt.bin** boot image. This avoids the "press any key" boot prompt
without injecting a key. The rebuild is ISO-9660 at level 3: that carries the
>4 GiB `sources/install.wim` as a multi-extent file and Windows reads it, even
though the vendor media uses UDF. The source ISO remains unchanged and pinned. Allow
roughly twice the source ISO size in additional staging space. If the no-prompt
boot image is absent, preparation fails clearly; it does not send keys in secret.

## Using the installed guest

Windows SSH commands run through `cmd.exe`, never `sh -lc`. COM1 carries installer
diagnostics rather than an interactive shell; use SSH, screenshots or the guest
agent to inspect the VM. Missing inputs keep the profile visible in the menu, with
a specific message explaining what blocks preparation.

Useful commands after the installer has completed and QEMU has exited:

```bash
./lab start windows-11
./lab ssh windows-11 -- 'ver'
./lab ssh windows-11 -- 'powershell.exe -NoProfile -Command "Get-Service sshd,QEMU-GA"'
./lab agent windows-11 ping
./lab agent windows-11 info
./lab agent windows-11 osinfo
./lab agent windows-11 ip
./lab agent windows-11 shutdown
```

`stop` waits 180 seconds for ACPI shutdown, then tries QGA and waits another 30
seconds, then runs `shutdown /s /t 0 /f` over the dedicated SSH key and waits 120
more: a Windows 11 desktop can ignore the ACPI power button entirely. Linux waits
60 + 30 seconds and never uses SSH, because it powers off on ACPI in seconds and
its lab user has no passwordless sudo. Only explicit `--force` permits host
signals; PID ownership is checked again and signals use a pidfd to avoid PID reuse.
`--force` still walks the whole graceful chain first, so it can take minutes; it
now says which stage it is waiting on.

## Sources and validation

Sources checked during implementation:

- [Microsoft answer files](https://learn.microsoft.com/en-us/windows-hardware/manufacture/desktop/update-windows-settings-and-scripts-create-your-own-answer-file-sxs?view=windows-11)
- [OpenSSH installation](https://learn.microsoft.com/en-us/windows-server/administration/openssh/openssh_install_firstuse)
- [Write-VolumeCache](https://learn.microsoft.com/en-us/powershell/module/storage/write-volumecache?view=windowsserver2025-ps)
- [QEMU Windows guest agent channel](https://github.com/qemu/qemu/blob/master/qga/channel-win32.c)

Earlier real Windows installation runs are recorded in [validation](VALIDATION.md).
The shared bootstrap/readiness checks added on 2026-09-19 have automated coverage,
but a fresh real-guest installation validating those changes is still pending.
Firmware/QMP tests and XML rendering tests do not validate OOBE, Windows Update
availability, or successful MSI installation.
