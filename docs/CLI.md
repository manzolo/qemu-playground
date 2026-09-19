# CLI reference

[← Project overview](../README.md) · [Usage](USAGE.md) · [Configuration](CONFIGURATION.md) · [CLI](CLI.md)

Run commands from the repository directory with `./lab`. Replace `VM` with
`lubuntu-26.04` or `windows-11`. Run `./lab COMMAND --help` for individual options.

| Command | Purpose |
| --- | --- |
| `doctor [--install]` | Tools, KVM access, RAM, disk and explicit apt proposal |
| `config init\|show` | Private configuration, values and provenance |
| `iso VM download\|verify\|redownload\|delete\|import` | Separate media actions; `import` takes `--source` |
| `prepare VM` | Dedicated key, qcow2 disk, installation seed and boot inputs |
| `install VM [--foreground] [--nudge]` | Bounded installer, passive timeline, retained failures |
| `up VM [--foreground]` | Install, boot and verify: SSH plus the Lubuntu desktop, or Windows bootstrap postconditions and a live QGA reply |
| `start VM` | Boot the disk with no installation media attached |
| `view VM` | Open the graphical console, when `LAB_VNC_PORT` is set |
| `stop VM [--force]` | ACPI, then QGA fallback; forced signals only when requested |
| `status` | Both profiles, owned PID, ISO verification, ports, usage and last result |
| `recover VM` | Read a lost attempt's verdict back out of the serial logs |
| `ssh VM` | Interactive session on the dedicated key, with a real tty |
| `ssh VM -- COMMAND` | Dedicated key, no password prompts; actual remote exit status |
| `agent VM ping\|info\|osinfo\|ip\|shutdown` | QGA without guest networking |
| `console VM [--timeout 300]` | Read-only Linux serial log |
| `shot VM [--no-open] [--nudge]` | Diagnostic capture; only explicit nudge sends Enter |
| `report VM [--pdf] [--open]` | Self-contained HTML and optional equivalent PDF; `--open` hands it to the desktop viewer |
| `clean VM TARGET... [--dry-run] [--yes]` | Enumerate and remove only selected paths |

Every command accepts `--dry-run`. Place SSH's host options **before** the VM name,
for example `./lab ssh --dry-run lubuntu-26.04 -- 'uname -a'`; everything after
`--` belongs to the guest. `--background` is also available for downloads and
preparation. SSH remains foreground to preserve its exit status.

For installation walkthroughs, background jobs, reports and cleanup examples, see
the [usage guide](USAGE.md).
