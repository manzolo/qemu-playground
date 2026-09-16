# Validation

Implementation checks, 2026-09-16:

- Standard-library unit/contract suite: **27 tests passed**; configuration and dry-run isolation,
  vendor hash checking and cache invalidation, XML/JSON seeds, SSH isolation and
  exit status, process identity, selective cleanup and symlink refusal, locks,
  QMP framing/timeouts, token-before-exit handling, failure evidence, passive
  screenshots, PNG encoding, explicit nudges, escaped self-contained reports.
- **3 real-tool smoke tests passed**, including Ubuntu preparation from a tiny
  synthetic ISO (kernel extraction, real qcow2 creation, SSH key generation and
  readable NoCloud seed). Real QEMU 10.2.1 TCG smoke tests for **both** VM command lines: firmware starts,
  process ownership is recognized, concurrent QMP clients serialize correctly,
  framebuffer becomes a PNG, report embeds it, and QMP shutdown exits the process.
  The Windows check includes OVMF Secure Boot firmware and a software TPM.
- Bash syntax and ShellCheck on both entrypoint and menu. The fzf fallback was
  opened in a real PTY; profile switching, Ctrl-C and explicit exit were exercised.
- HTML and PDF rendering passed with installed optional dependencies; an ignored
  `out/example.pdf` accompanies the versioned HTML example.
- Dry runs for Ubuntu and Windows without ISO images or optional Python packages.

The QEMU smoke tests create fresh temporary 64 MiB disks and boot firmware only.
They do not use a developer's VM or install an operating system. Unix sockets are
blocked inside the development sandbox, so socket and QEMU tests require normal
host execution. CI on ordinary Linux runners can run the unit tests without QEMU.

Not yet validated on a real guest:

- Ubuntu 26.04 installation with the pinned ISO, apt access and post-install SSH.
- Windows Setup/OOBE, Features on Demand, signed QGA MSI installation and its COM2
  service channel in the installed OS.
- Full installation timelines, timeout recovery from a real stalled installer,
  and real guest graceful shutdown timings.
- Interactive tmux layout on a host with tmux installed.

To perform acceptance testing on a KVM host:

```bash
./lab doctor
./lab config init
./lab up ubuntu-26.04 --foreground
./lab ssh ubuntu-26.04 -- 'uname -a'
./lab shot ubuntu-26.04
./lab stop ubuntu-26.04
./lab report ubuntu-26.04 --pdf
# Supply Windows ISO and QGA inputs as documented in WINDOWS.md, then:
./lab up windows-11 --foreground
./lab ssh windows-11 -- 'ver'
./lab agent windows-11 ping
./lab agent windows-11 osinfo
./lab shot windows-11
./lab stop windows-11
./lab report windows-11 --pdf
```

Use the resulting reports as acceptance evidence. A missing agent response should
be described alongside the latest guest screen, not used to guess why a VM failed
to boot. Preserve the first failed attempt when retrying, so the report can explain
an eventual second-attempt success rather than replacing the history with green.
