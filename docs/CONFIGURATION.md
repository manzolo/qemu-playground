# Configuration

[← Project overview](../README.md) · [Usage](USAGE.md) · [Configuration](CONFIGURATION.md) · [CLI](CLI.md)

All settings are listed in [`.env.example`](../.env.example). Initialize your
private configuration, then inspect the effective values:

```bash
./lab config init
./lab config show
```

## Console password

`./lab config init` prints the console password it generates, something like
`Virtio-6258`: short enough to type at the graphical console, with an upper case
letter, a lower case letter and a digit so Windows accepts it even where the local
complexity policy is on. It is stored in `.env` (0600, gitignored) and neither
guest ever accepts it over SSH, which is key-only.

## Graphical console and unattended runs

A graphical console is available: `LAB_VNC_PORT` defaults to 5940 (Windows takes
the next port, and 5940 stays clear of libvirt, which hands out 5900 upwards),
bound to localhost, opened with `lab view VM`. Set it to 0 to switch it off.

Exposing a console and using one are different claims, and the report keeps them
apart. Opening the port is recorded and reported as "a graphical console was
exposed; no client connected to it". During an installation the lab also asks QEMU
whether anyone is actually attached, and only when a client connects does the
verdict say the run is no longer provably unattended. Passive screenshots remain
the default way to watch: they cannot type.

## Desktop, language and audio

The installed Lubuntu guest always includes LXQt and a graphical login. `LAB_AUTOLOGIN`
is `1` by default, so the lab user arrives straight in the LXQt session; set it to `0`
to stop at the SDDM greeter, and sign in there with the user and console password from
`.env`. Autologin means anyone who can reach a running VM's graphical console gets the
session, so turn it off if that console is not yours alone. SSH is key-only either way,
and the password is still what `sudo` asks for.

SDDM does not read the system's keyboard
configuration for its own greeter, so the lab writes it a drop-in that applies
`LAB_KEYBOARD` there too — without it the greeter offers `us` while the installed system
is Italian, and the password is typed at exactly that screen. `LAB_LOCALE` is likewise
restated twice, because the desktop and the greeter are reached by different paths.
`DefaultEnvironment=` on the systemd manager covers services, and so SDDM and the
greeting; `/etc/xdg/lxqt/session.conf` covers the session, which SDDM starts through
PAM rather than as a service and which the manager's environment never reaches. Every
system-wide locale file loses to systemd's own `LANG=C.UTF-8`, so without both, one
half or the other comes up in English on an Italian system.

An autostart entry runs
`xset s off -dpms`, because X blanks the screen after ten minutes and passive
screenshots are the only way the lab watches a guest it will not type into. The old `LAB_DESKTOP`
setting is accepted in existing `.env` files but ignored; it cannot disable Lubuntu's
desktop.

`LAB_AUDIO` names a QEMU audio backend (`pipewire`, `pa`,
`alsa`...) to give the guest a sound card played through the host's daemon; it is
`none` by default, because a headless host has no daemon and naming a backend that
is not there stops QEMU from starting.

## Configuration and networking

The lab reads `.env` as data; it never executes shell substitutions or sources it.
Environment variables do not silently override configuration. `.env.example`
lists all settings. Lubuntu uses port 2400 and Windows 2401 by default, bound only
to localhost. Adjust `LAB_SSH_PORT` to move both. KVM is the default;
`LAB_ACCEL=tcg` is available for slow emulation and firmware diagnostics.

## Dedicated SSH keys

SSH uses `keys/id_ed25519`, `BatchMode=yes`, `IdentitiesOnly=yes`, no personal
configuration and `keys/known_hosts`. A changed guest host key is deliberately
rejected. After intentionally replacing a guest disk, remove only that guest's
old entry:

```bash
ssh-keygen -R '[127.0.0.1]:2400' -f keys/known_hosts
```
