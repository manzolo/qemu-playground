# Validation

Implementation checks and the first real-guest run, 2026-09-16.

## Automated suite

- Standard-library unit/contract suite: **45 tests passed**; configuration and dry-run isolation,
  vendor hash checking and cache invalidation, XML/JSON seeds, SSH isolation and exit status,
  process identity, selective cleanup and symlink refusal, locks, QMP framing/timeouts,
  token-before-exit handling, failure evidence, passive screenshots, PNG encoding, explicit
  nudges, escaped self-contained reports.
- **3 real-tool smoke tests passed**, including Ubuntu preparation from a tiny synthetic ISO
  (kernel extraction, real qcow2 creation, SSH key generation and readable NoCloud seed).
  Real QEMU 10.2.1 TCG smoke tests for **both** VM command lines: firmware starts, process
  ownership is recognized, concurrent QMP clients serialize correctly, framebuffer becomes a
  PNG, report embeds it, and QMP shutdown exits the process. The Windows check includes OVMF
  Secure Boot firmware and a software TPM.
- Bash syntax and ShellCheck on both entrypoint and menu. The fzf fallback was opened in a real
  PTY; profile switching, Ctrl-C and explicit exit were exercised.
- HTML and PDF rendering passed with installed optional dependencies.
- Dry runs for Ubuntu and Windows without ISO images or optional Python packages.

The QEMU smoke tests create fresh temporary 64 MiB disks and boot firmware only. They do not use
a developer's VM or install an operating system. CI on ordinary Linux runners runs the unit tests
without QEMU.

## Real guests, 2026-09-16

Both profiles were installed unattended on a KVM host and then reached over SSH with the
dedicated key.

### Ubuntu 26.04 — one attempt, passed

Installation `passed (unattended)` in **371 s**. Disk **6,471,417,856 bytes**. Afterwards, on the
booted guest:

```
$ ./lab ssh ubuntu-26.04 -- 'uname -a; lsb_release -ds'
Linux playground 7.0.0-31-generic #31-Ubuntu SMP PREEMPT_DYNAMIC Sat Aug  1 04:26:38 UTC 2026 x86_64 GNU/Linux
Ubuntu 26.04 LTS
```

Evidence in `out/ubuntu-26.04.html`. A second `install` on the same disk was refused —
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
against 2,021 s), so the flow is reproducible rather than lucky. Ubuntu, reinstalled
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

## Continuous integration

Every push runs the standard-library suite and the dry runs on Python 3.10 and 3.14,
plus the real-QEMU smoke tests under **TCG**, which need no `/dev/kvm`: firmware boots
for both profiles, concurrent QMP clients, a screenshot encoded to PNG and embedded in
a report, and a seed built from a synthetic ISO. Seconds, and no guest is installed.

A whole Ubuntu installation with no KVM at all lives in a separate workflow, run on
a `v*` tag and from a manual button. **It has never run on a GitHub runner**: the same
install takes 370 s under KVM here and emulation is slower by a large factor, the ISO
is 2.7 GB and the disk grows past 6 GB on a runner with little spare space. Expect the
first real run to need its timeout and its disk cleanup tuned. It uploads `out/`, the
serial log and the event log whatever the outcome, so a failure arrives as evidence.

## Still not validated

- **Graceful shutdown of an installed Windows guest**: the only measurement so far is the 220 s
  timeout above, taken on a VM left over from a failed installation — not a representative one.
- **Timeout recovery from a genuinely stalled installer.** The three failures above were
  terminations and a reported failure, not a hang.
- **The interactive tmux layout**: tmux is not installed on this host, so only the fzf fallback
  has been exercised.
- **Windows Features on Demand** beyond the OpenSSH capability actually installed here.
- Behaviour on media other than the pinned Ubuntu ISO and the imported Italian x64 Windows ISO.

## To repeat the acceptance test on another KVM host

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

Use the resulting reports as acceptance evidence. A missing agent response should be described
alongside the latest guest screen, not used to guess why a VM failed to boot. Preserve the first
failed attempt when retrying, so the report can explain an eventual second-attempt success rather
than replacing the history with green.
