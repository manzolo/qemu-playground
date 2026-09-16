# Windows 11 profile

This profile requires a Windows license appropriate to your use and installation
media matching the pinned **Italian x64** vendor checksum. The answer file selects
`Windows 11 Pro`; it does not activate Windows or bypass licensing/hardware checks.

1. Download the matching ISO from [Microsoft](https://www.microsoft.com/en-us/software-download/windows11).
2. Run `./lab config init` and inspect `.env`.
3. Obtain a signed x64 QEMU guest agent MSI from a trusted distributor. The
   [Fedora virtio-win project](https://github.com/virtio-win/virtio-win-pkg-scripts/blob/master/README.md)
   documents its distribution channels. Record the distributor-provided expected
   digest and its provenance, not a checksum invented from an untrusted download.
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
without injecting a key. The source ISO remains unchanged and pinned. Allow
roughly twice the source ISO size in additional staging space. If the no-prompt
boot image is absent, preparation fails clearly; it does not send keys in secret.

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
seconds. Linux waits 60 + 30 seconds. Only explicit `--force` permits host signals;
PID ownership is checked again and signals use a pidfd to avoid PID reuse.

Sources checked during implementation:

- [Microsoft answer files](https://learn.microsoft.com/en-us/windows-hardware/manufacture/desktop/update-windows-settings-and-scripts-create-your-own-answer-file-sxs?view=windows-11)
- [OpenSSH installation](https://learn.microsoft.com/en-us/windows-server/administration/openssh/openssh_install_firstuse)
- [Write-VolumeCache](https://learn.microsoft.com/en-us/powershell/module/storage/write-volumecache?view=windowsserver2025-ps)
- [QEMU Windows guest agent channel](https://github.com/qemu/qemu/blob/master/qga/channel-win32.c)

Full unattended Windows validation is pending access to these licensed media and
a suitable host. Firmware/QMP tests and XML rendering tests do not validate OOBE,
Windows Update availability, or successful MSI installation.
