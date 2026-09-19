# Development

[← Project overview](../README.md) · [Usage](USAGE.md) · [Configuration](CONFIGURATION.md) · [CLI](CLI.md)

Python 3.10+ and the standard library suffice for the core. Scripts explicitly
use Bash even when the user's shell is zsh. To add the command to your shell, add
this directory to `PATH` in `~/.zshrc`.

```bash
python3 -m unittest discover -s tests -v
bash -n lab scripts/menu.sh
shellcheck lab scripts/menu.sh
# Optional real QEMU firmware-only checks, no ISO or KVM:
python3 -m unittest discover -s tests -p smoke_qemu.py -v
```

Normal tests use temporary fixtures and do not read your `.env`, keys, media or VM
disks. CI runs the unit tests, shell checks and dry runs, plus a separate QEMU/TCG
firmware smoke job without installation media. A tag-triggered or manually
started workflow runs a complete Lubuntu installation under TCG.
See [decisions](DECISIONS.md), [checksum provenance](CHECKSUMS.md) and
[validation](VALIDATION.md). The original local specification remains in `PROMPT.md`, excluded from Git because it contains machine-specific paths.

The workflows live in [`.github/workflows`](../.github/workflows).
