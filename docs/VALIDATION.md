# Validation

Implementation checks and real-guest runs. The `ubuntu-26.04` results are from 2026-09-16;
the `lubuntu-26.04` profile that replaced it was introduced on 2026-09-19 and installed on a
real guest the same day — with a caveat about the host-side record, below.

## Automated suite

- Standard-library unit/contract suite: **69 tests passed**; configuration and dry-run isolation,
  vendor hash checking and cache invalidation, XML/JSON seeds, SSH isolation and exit status,
  process identity, selective cleanup and symlink refusal, locks, QMP framing/timeouts,
  token-before-exit handling, failure evidence, passive screenshots, PNG encoding, explicit
  nudges, escaped self-contained reports, the required Lubuntu desktop in the seed (including
  a legacy `LAB_DESKTOP` value being ignored rather than honoured) and the guided menu's
  recommendation for each lab state, the SDDM drop-in and greeter keyboard, autologin
  being configurable and checked only when on, and verdict recovery from an archived
  serial log including the three states where it refuses to guess, and a verdict being
  qualified only by its own attempt's console and intervention events.
- **3 real-tool smoke tests passed**, including Lubuntu preparation from a tiny synthetic ISO
  (kernel extraction, real qcow2 creation, SSH key generation and readable NoCloud seed).
  Real QEMU 10.2.1 TCG smoke tests for **both** VM command lines: firmware starts, process
  ownership is recognized, concurrent QMP clients serialize correctly, framebuffer becomes a
  PNG, report embeds it, and QMP shutdown exits the process. The Windows check includes OVMF
  Secure Boot firmware and a software TPM.
- Bash syntax and ShellCheck on both entrypoint and menu. The fzf fallback was opened in a real
  PTY; profile switching, Ctrl-C and explicit exit were exercised.
- HTML and PDF rendering passed with installed optional dependencies.
- Dry runs for Lubuntu and Windows without ISO images or optional Python packages.

The QEMU smoke tests create fresh temporary 64 MiB disks and boot firmware only. They do not use
a developer's VM or install an operating system. CI on ordinary Linux runners runs the unit tests
without QEMU.

## Real guests

### Lubuntu 26.04 — installed in the guest, unrecorded on the host

**The guest installation passed. The lab did not record it.** Both halves matter.

In the guest, on a KVM host: QEMU launched at 14:24:30, the installer emitted
`LAB_OK_7b4dd81f43343d44264cdf42` and the machine powered itself off at about 14:37:54 —
roughly **800 s** (the watcher polled every 15 s, and the kernel's own last line is
`[ 803.533885] reboot: Power down`). Disk **11,940,331,520 bytes**, against 6,471,417,856 for
the Server profile it replaces: the desktop costs about **1.85x** the disk and a little over
twice the wall clock.

The evidence that the desktop gate works is in `work/lubuntu-26.04/serial.log`, where all four
late-commands reach `finish:` in order:

```
finish:  subiquity/Late/run_user_supplied/command_0: ... systemctl enable serial-getty@ttyS0.service
finish:  subiquity/Late/run_user_supplied/command_1: ... systemctl enable sddm.service
finish:  subiquity/Late/run_user_supplied/command_2: ... systemctl set-default graphical.target
finish:  subiquity/Late/run_user_supplied/command_3: ... dpkg-query ... lxqt-session ... sddm.service
```

`command_3` is `desktop.lubuntu_check()`, which carries no `|| true`; the token is written only
after it returns. So the token here means what the profile claims it means, and a run where the
desktop was missing would have taken the error path instead.

**What went wrong on the host.** `work/lubuntu-26.04/events.jsonl` stops at the `qemu`/`console`
events of 14:24:30. There is no `installation` event, no `command-end` for the install, and no
`out/lubuntu-26.04.html`. The background installation worker was killed — the menu it was
launched from was restarted at 14:28 and 14:29 — so nothing was left watching the serial log
for the token or waiting for QEMU to exit. `attempt.json` survives without a verdict, which is
why the lab correctly reports *"This disk has an unvalidated installation attempt"*: from its own
records, it cannot tell this run from a failure.

This is a gap the profile change does not close, and it should not be papered over by the fact
that a human read the log afterwards. The two facts the lab treats as proof — the token and a
spontaneous exit — were both produced and both left on disk, and the verdict was lost anyway
because only a live process was looking. Detaching by default is documented; losing the evidence
when the parent dies is not, and a background command returning zero already means only that a
worker was launched. Recovering a verdict from `serial.log` and the QEMU exit after the fact is
not implemented.

**The installed disk was then booted and checked.** `start` brought it up, key-authenticated SSH
answered within 10 s, and the guest reported:

```
$ ./lab ssh lubuntu-26.04 -- 'uname -a; lsb_release -ds; systemctl get-default; systemctl is-active display-manager'
Linux playground 7.0.0-31-generic #31-Ubuntu SMP PREEMPT_DYNAMIC Sat Aug  1 04:26:38 UTC 2026 x86_64 GNU/Linux
Ubuntu 26.04 LTS
graphical.target
active
```

`desktop.lubuntu_check(running=True)` — the exact string `up` sends — exited zero against the
running guest, and the screenshot in `docs/images/lubuntu-26.04-installed.png` is that machine's
SDDM greeter offering the `Lubuntu` session for `labuser`. `lsb_release` says `Ubuntu 26.04 LTS`
because the bootstrap medium is Ubuntu Server; the desktop on top is what the profile adds.

**The greeter's keyboard was wrong, and was fixed.** With `LAB_KEYBOARD=it` the installed system
was configured correctly — `XKBLAYOUT="it"` in `/etc/default/keyboard`, `X11 Layout: it` from
`localectl` — but SDDM's greeter offered `Layout: us`: it starts the greeter on an X server of
its own and reads neither. The seed now writes `/etc/sddm.conf.d/90-lab.conf` and a
`DisplayCommand` script that runs `setxkbmap`. Applied to the running guest, the greeter came
back reading `it` and in Italian.

**Autologin was added and checked in both directions.** With the drop-in in place the guest boots
straight into the LXQt session, which is the screenshot in `docs/images/`. The check that proves
it is not "a session exists for the lab user" — the SSH connection running the check makes one of
those on any guest — but the *active session on seat0*, which must belong to the lab user. Both
outcomes were exercised on the live guest:

```
with [Autologin]:     seat0 active session = labuser   -> LAB_AUTOLOGIN=1 check passes
without [Autologin]:  seat0 active session = sddm      -> LAB_AUTOLOGIN=1 check fails,
                                                          LAB_AUTOLOGIN=0 check passes
```

The full `lubuntu_check(cfg, running=True)` string — thirteen conditions, the one `up` sends —
was run against the booted guest and exited zero.

**The lost verdict was recovered.** `./lab recover lubuntu-26.04` was run against the real orphaned
attempt described above. It searched three serial logs, found `LAB_OK_7b4dd81f43343d44264cdf42` in
the archived `serial-1789821625642098850.log`, recorded
`passed (unattended, recovered; QEMU exit not observed)`, wrote `out/lubuntu-26.04.html`, and the
guided menu moved from step 4 to step 5. Run a second time it refused, naming the verdict already
there.

### Lubuntu 26.04, attempt 2 — installed from the seed, end to end

The disk and seed were then cleaned and the whole thing rebuilt with `./lab up lubuntu-26.04
--foreground`, so that the SDDM drop-in, the greeter keyboard and autologin were written **by the
installer** rather than applied afterwards. That is the run that validates the profile.

| | |
|---|---|
| Installation | `passed (unattended)` in **781.63 s** |
| Disk | **11,526,733,824 bytes** |
| `ssh-ready` | passed |
| `desktop-ready` | passed, 14 s after the installed guest was booted |
| Verdict in the report | carries a caveat — see below |

The guest was then read back over SSH. `/etc/sddm.conf.d/90-lab.conf` holds the `[Autologin]`
block and the `DisplayCommand`; `/etc/sddm/lab-xsetup` still carries the
`# Written by qemu-playground` line, which is what proves these came from `xsetup_script()`
through the seed and not from the earlier hand-patching. The active session on seat0 reads
`labuser`, so autologin ran from a cold boot, and `localectl` reports `X11 Layout: it`. The
screenshot in `docs/images/` is that machine.

**The verdict carries a caveat, and should.** At 15:22:08, while the installer was running,
something attached to the graphical console, so the report reads:

```
passed (unattended) - a client connected to the graphical console, so the run is not
provably unattended - attempt 2; previous outcomes retained below
```

Nothing typed anything — there is no `intervention` event — but the lab cannot know that, and
this is exactly the distinction between *exposed* and *used* that the console was given a
separate event for. The installation is proven; its unattendedness is, by the lab's own rule,
not proven for this particular run. The recovered verdict from attempt 1 is retained above it in
the same report.

**Observed and not chased:** this session's desktop labels come up in English although
`LAB_LOCALE=it_IT.UTF-8` and the greeter, on the earlier guest, appeared in Italian once SDDM had
been restarted. The system locale and keyboard are correct; only the session's own translation is
not. Not investigated, and nothing in the lab configures it.

### Lubuntu 26.04, attempt 3 — provably unattended

Attempt 2's only blemish was that something had attached to its graphical console, so the run was
rebuilt once more with `LAB_VNC_PORT=0`, which leaves QEMU with no `-vnc` argument and nothing to
attach to. Verified against the live process rather than the recorded command line, since the
recorded one on disk still belonged to the previous run at the time.

| | |
|---|---|
| Installation | `passed (unattended)` in **750.42 s** |
| `ssh-ready`, `desktop-ready` | both passed, 14 s after the installed guest was booted |
| `console` / `console-client` events | none |
| Verdict in the report | `passed (unattended) — attempt 3; previous outcomes retained below` |

That is the first run of this profile whose unattendedness is proven rather than merely likely.
The configuration is otherwise the default one, autologin included.

**It took a report fix to say so.** The first report of attempt 3 still read *"a client connected
to the graphical console, so the run is not provably unattended"* although attempt 3 never exposed
one: the verdict was qualified from the whole event history, so a console opened during one run
went on contradicting every run after it. Scoping it to the previous verdict was not enough
either — `up` boots the installed guest as soon as it records one, and that boot's own `console`
event landed 0.4 s past the boundary, which is exactly how the second wrong answer
(*"a graphical console was exposed; no client connected"*) was produced. The attempt's real start
is already recorded, because the event carries its duration; the window now runs from there.
Regression test included, and the previous attempts remain visible in the report either way.

**The session's language was a real defect, and the first fix for it was wrong.** The desktop came
up in English under `LAB_LOCALE=it_IT.UTF-8` while `/etc/default/locale`, `/etc/locale.conf` and
an SSH session all carried the right value. `/etc/environment` was tried first, on the argument
that `pam_env` demonstrably delivers that file — the session's `PATH` comes from it — and it
passed when SDDM was restarted by hand.

Attempt 4 then failed: `passed (unattended)` in 811.12 s, and `up` timed out after 300 s with the
desktop check red. Walking the check one condition at a time, fourteen passed and the fifteenth
did not, and the session turned out to hold `LANG=C.UTF-8` rather than nothing — the value
`systemctl show-environment` reports. systemd's manager environment reaches the session and beats
`pam_env`, and restarting the display manager leaves that out of the path the variable travels, so
only a cold boot can see it. The fix now writes `/etc/xdg/lxqt/session.conf`, whose `[Environment]`
block LXQt applies inside `lxqt-session` itself. Retested by stopping and starting the VM rather
than the service: the session comes up `it_IT.UTF-8` and the desktop in Italian.

Two things are worth keeping from that: the check that caught it reads `LANG` out of the live
session's `/proc/<pid>/environ` rather than out of a file, which is why a correct file could not
hide a broken session; and the failure was only reachable through a real installation — nothing
short of one would have put systemd's value in the way. Separately, no `language-pack-*` package
is installed at all, though 31 LXQt `-l10n` packages are; the desktop is translated regardless.

### Lubuntu 26.04, attempt 5 — the whole profile, from the seed, unwatched

| | |
|---|---|
| Installation | `passed (unattended)` in **1,092.46 s** |
| `ssh-ready`, `desktop-ready` | both passed, 17 s after the installed guest booted |
| `console` / `console-client` events | none (`LAB_VNC_PORT=0`) |
| Verdict | `passed (unattended) — attempt 5; previous outcomes retained below` |

Read back over SSH, with nothing applied by hand: `/etc/xdg/lxqt/session.conf` holds
`[Environment]` and `LANG=it_IT.UTF-8`; the live session holds `LANG=it_IT.UTF-8` rather than
`C.UTF-8`; `/etc/environment` holds only `PATH`, the abandoned approach being gone; seat0's active
session is `labuser`; `X11 Layout: it`. The screenshot in `docs/images/` is the frame `up` took
itself on reaching `desktop-ready`, and the desktop in it is in Italian.

The installation time is 1,092 s against 750 s for attempt 3, on the same host with the same
media. Archive throughput is the obvious suspect and was not measured, so treat the figure as a
range rather than a benchmark.

**The desktop blanked, and that is now fixed too.** Sixteen minutes after the session started,
`shot` returned a black framebuffer and the lab did what it is built to do — stored the serial
log tail instead, captioned as such. `xset -q` against the live session named the cause exactly:

```
Screen Saver:  timeout: 600   prefer blanking: yes
DPMS: Standby 600  Suspend 600  Off 600 ... DPMS is Enabled ... Monitor is Off
```

Screenshots are how this lab watches a guest it refuses to type into, so an idle desktop stops
being observable, and waking it with a keystroke would mark an otherwise unattended run assisted.
The seed now writes `/etc/xdg/autostart/qemu-playground-noblank.desktop`, which runs
`xset s off -dpms` at session start. Verified the way the locale fix should have been the first
time: the entry was written, the **VM** stopped and started, and the freshly booted session came
up with `timeout: 0` and `DPMS is Disabled` without anything else being done to it. Falsified too
— `xset +dpms` on the running session turns the check red, so it is not passing vacuously. The
running check reads the X server through the session's own `DISPLAY` and `XAUTHORITY`, not the
file, for the same reason the locale check reads `/proc/<pid>/environ`.

The check is 18 conditions now, all of them passing on a cold-booted guest.

So: the profile is validated on a real guest end to end — an unattended installation, proven
unattended, that writes its own desktop configuration, and a graphical session reached
automatically from a cold boot. What that session gets wrong is its own language.

### Ubuntu Server 26.04 (superseded profile) — one attempt, passed

Installation `passed (unattended)` in **371 s**. Disk **6,471,417,856 bytes**. Afterwards, on the
booted guest:

```
$ ./lab ssh ubuntu-26.04 -- 'uname -a; lsb_release -ds'
Linux playground 7.0.0-31-generic #31-Ubuntu SMP PREEMPT_DYNAMIC Sat Aug  1 04:26:38 UTC 2026 x86_64 GNU/Linux
Ubuntu 26.04 LTS
```

Evidence was written to `out/ubuntu-26.04.html`. A second `install` on the same disk was refused —
*"Disk already used for an installation attempt. Preserved by default."* — which is the retention
policy working as designed, not a defect.

### Windows 11 — four attempts, the fourth passed, then reproduced

| # | ended | duration | outcome |
|---|---|---|---|
| 1 | 14:23 | 2,560 s | failed — QEMU exited without the completion token |
| 2 | 15:03 | 945 s | failed — same |
| 3 | 16:55 | 2,522 s | failed — **the installer emitted its own failure token** |
| 4 | 19:31 | **2,021 s** | **passed (unattended)** |

Disk **26,072,514,560 bytes**. Afterwards, on the booted guest:

```
$ ./lab ssh windows-11 -- 'ver'
Microsoft Windows [Versione 10.0.26200.8037]
$ ./lab agent windows-11 ping
{}
$ ./lab agent windows-11 osinfo
  "version-id": "11", "variant-id": "client", "kernel-version": "10.0", "id": "mswindows"
```

| 5 | 22:52 | **2,034 s** | **passed (unattended)**, first try, from a wiped disk |

Evidence in `out/windows-11.html`, which keeps every attempt.

Run 5 is the one that makes the rest mean something: both profiles were wiped and
reinstalled from scratch, in parallel on the same host, with every fix already in
place. It passed on the first attempt in essentially the same time as run 4 (2,034 s
against 2,021 s), so the flow is reproducible rather than lucky. Ubuntu Server, reinstalled
alongside it, passed in 371 s again.

**The progression matters more than the final green.** Attempts 1 and 2 died before the guest
could say anything at all. Attempt 3 is the first in which the seed reached the bootstrap and the
installer reported *its own* failure — that is what made the last fix possible. A report showing
only attempt 4 would hide the part that did the work.

**The successful run was not assisted.** The only `--nudge` of the day (18:10:57) was sent to the
leftover VM of attempt 3 during inspection, not inside attempt 4, which carries no `intervention`
event. Anyone can check:

```
grep '"kind": "intervention"' work/windows-11/events.jsonl
```

Also observed and not smoothed over: stopping attempt 3's VM with `./lab stop` **timed out after
220 s** waiting for guest shutdown and needed `stop --force`.

### Lubuntu 26.04, attempt 6 — a correct guest, and a check that called it broken

Installed from the seed with `LAB_VNC_PORT=0`: `passed (unattended)` in **1,205.64 s**, verdict
`passed (unattended) — attempt 6`, no console events. Then `up` timed out after 300 s again, and
there is no `ssh-ready` or `desktop-ready` event for it.

The guest was fine. Walking the check condition by condition, seventeen passed — including the
new `xset` one, so the autostart entry written by the seed had disabled blanking on its own — and
the locale condition failed. `lxqt-session` had no `LANG` in its environment at all; `lxqt-panel`,
`pcmanfm-qt` and `lxqt-globalkeys` all had `LANG=it_IT.UTF-8`, and the desktop was in Italian.

`/proc/<pid>/environ` is the environment a process was **started** with. LXQt applies its
`[Environment]` block by setting variables for the programs it launches, so the session leader
itself never shows it. That explains all three readings this profile has produced: nothing here,
`C.UTF-8` on attempt 4, and on attempt 5 the right answer by coincidence — the worst of them,
because it was taken as proof. The check now reads `lxqt-panel`. `DISPLAY` and `XAUTHORITY` keep
reading `lxqt-session`, since SDDM sets those before exec.

With that corrected, all 18 conditions pass against attempt 6's guest, which was installed
entirely from the seed and touched by nothing afterwards. The screenshot in `docs/images/` is that
guest — and it was taken **39 minutes** after the session started, which is the blanking fix
demonstrating itself: before it, ten minutes was enough to turn `shot` into a serial log dump.

What is still missing is a run where `up` itself goes green, because the only installation made
since the check reached 18 conditions is the one the bug failed.

## Continuous integration

Every push runs the standard-library suite and the dry runs on Python 3.10 and 3.14,
plus the real-QEMU smoke tests under **TCG**, which need no `/dev/kvm`: firmware boots
for both profiles, concurrent QMP clients, a screenshot encoded to PNG and embedded in
a report, and a seed built from a synthetic ISO. Seconds, and no guest is installed.

A whole Lubuntu installation with no KVM at all lives in a separate workflow, run on
a `v*` tag and from a manual button. **It has never run on a GitHub runner**: the Server
install it grew out of took 370 s under KVM here, emulation is slower by a large factor,
the ISO is 2.7 GB and the disk grows past 6 GB on a runner with little spare space. The
desktop makes all three worse — more packages to fetch from the archive and more disk —
and the job now runs `up`, so it also waits for the installed guest to boot and answer
the desktop check. Expect the first real run to need its timeout and its disk cleanup
tuned. It uploads `out/`, the serial log and the event log whatever the outcome, so a
failure arrives as evidence.

## Still not validated

- **Graceful shutdown of an installed Windows guest**: the only measurement so far is the 220 s
  timeout above, taken on a VM left over from a failed installation — not a representative one.
- **Timeout recovery from a genuinely stalled installer.** The three failures above were
  terminations and a reported failure, not a hang.
- **The interactive tmux layout**: tmux is not installed on this host, so only the fzf fallback
  has been exercised.
- **Windows Features on Demand** beyond the OpenSSH capability actually installed here.
- **`LAB_AUTOLOGIN=0` on a real installation.** It is covered by unit tests and was exercised
  against a running guest, but no guest has been installed with it off, and nobody has typed the
  password at the corrected `it` greeter.
- **Why systemd's manager environment is `C.UTF-8`** on a guest whose `/etc/locale.conf` is not,
  and whether that is worth correcting at the source rather than worked around in the session.
- **A green `up` for this profile since the check reached 18 conditions.** Attempt 6's guest
  passes all of them, but the run that produced it did not: see below.
- **Why attempt 5 took 1,092 s against attempt 3's 750 s** on the same host and media.
- **Whether a console exposed but unused is worth the caveat it prints.** Attempt 3 avoided it by
  turning the console off entirely, which is not what most runs will do.
- **`apt.fallback: abort` actually aborting**: no run has yet been made with the archive
  unreachable, so the failure path this profile depends on has been read, not exercised.
- Behaviour on media other than the pinned Ubuntu bootstrap ISO and the imported Italian x64
  Windows ISO.

## To repeat the acceptance test on another KVM host

```bash
./lab doctor
./lab config init
./lab up lubuntu-26.04 --foreground
./lab ssh lubuntu-26.04 -- 'uname -a; systemctl get-default; systemctl is-active display-manager'
./lab shot lubuntu-26.04          # should show the SDDM/LXQt login, not a text console
./lab stop lubuntu-26.04
./lab report lubuntu-26.04 --pdf
# Supply Windows ISO and QGA inputs as documented in WINDOWS.md, then:
./lab up windows-11 --foreground
./lab ssh windows-11 -- 'ver'
./lab agent windows-11 ping
./lab agent windows-11 osinfo
./lab shot windows-11
./lab stop windows-11
./lab report windows-11 --pdf
```

Use the resulting reports as acceptance evidence. A missing agent response should be described
alongside the latest guest screen, not used to guess why a VM failed to boot. Preserve the first
failed attempt when retrying, so the report can explain an eventual second-attempt success rather
than replacing the history with green.
