# Using the lab

[← Project overview](../README.md) · [Usage](USAGE.md) · [Configuration](CONFIGURATION.md) · [CLI](CLI.md)

[Menu](#interactive-menu) · [Lubuntu](#lubuntu-step-by-step) · [Windows](WINDOWS.md) · [Background jobs](#background-jobs-and-recovery) · [Reports](#evidence-and-reports) · [Cleanup](#cleanup-and-retries)

## Interactive menu

```bash
./lab
```

The menu opens a guided path: **environment → ISO → preparation → installation →
use the VM**. It shows the current step, a short explanation and one recommended
action based on the selected profile's files and running operations. Completed
steps are skipped; refresh after a background operation to discover the next
step. An unvalidated installation attempt points to reports and logs, preserving
the disk for inspection.

The home screen also offers VM access, reports, profile selection, and settings
and maintenance. The latter contains small submenus for configuration, media,
machine operations and cleanup. Unavailable actions remain muted in those
submenus; `Tab` reveals their reason and the exact CLI command.

| Shortcut | Action |
| --- | --- |
| `Tab` | Toggle the full-width command preview, hidden initially |
| `Ctrl-Y` | Show the command as plain terminal text for copying |
| `Ctrl-P` | Choose a profile |
| `Ctrl-R` / `F5` | Refresh the current state and recommended step |
| `Ctrl-L` | Toggle live logs in tmux, or open the selected VM's log |
| `Esc` | Go back one screen; exit from home |
| `Ctrl-Q` | Exit from any screen |
| `Ctrl-C` | Return to the current screen |

Commands in the preview have no `$` prompt and use an absolute path, so they work
from another shell's directory. After `Ctrl-Y`, select the text and use your
terminal's copy shortcut, including when lines wrap. Each submenu also has a Back
entry, and home has an Exit entry. After a command runs, any key returns to the
wizard, which recalculates the next step.

The menu is English by default; `LAB_LANG=it` in `.env` translates its labels,
categories and blocker reasons. Nothing else changes: commands, logs, events and
the HTML report stay English, and the report keeps its Italian quick guide.
Logs stay hidden until needed: `Ctrl-L` toggles a small live log pane with tmux.
Without tmux, it opens the selected VM's log; `Ctrl-C` returns to the menu.
`LAB_NO_TMUX=1 ./lab` selects that fallback explicitly.

## Lubuntu, step by step

```bash
./lab iso lubuntu-26.04 download      # curl resume + pinned SHA-256 verification
./lab iso lubuntu-26.04 verify
./lab prepare lubuntu-26.04
./lab install lubuntu-26.04          # background, timeline recorded automatically
./lab status
# Wait for a passed installation and spontaneous QEMU exit, then:
./lab start lubuntu-26.04
./lab ssh lubuntu-26.04 -- 'uname -a'
./lab shot lubuntu-26.04             # capture via QMP, convert to PNG, xdg-open
./lab console lubuntu-26.04          # read-only serial log, bounded follow
./lab report lubuntu-26.04
./lab stop lubuntu-26.04
```

Or run the same steps as one operation:

```bash
./lab up lubuntu-26.04 --dry-run     # no writes, downloads or processes
./lab up lubuntu-26.04 --foreground  # includes boot and key-authenticated SSH check
```

Lubuntu boots the bootstrap ISO's extracted kernel/initrd directly with
`autoinstall` and NoCloud seed media. No menu keys are injected. The installer
installs `lubuntu-desktop`, SSH and the QEMU guest agent, enables SDDM and sets
`graphical.target`. It verifies the desktop packages and session before emitting
the completion marker, after `sync` and a block-device flush. The host then
waits for QEMU to exit on its own. `up` also boots the installed system and checks
that SDDM is active over key-authenticated SSH. Failure and timeout keep
the VM and disk available, take a final screenshot where possible, and generate
an HTML report.

For Windows media, firmware, guest agent requirements and installation steps,
see the [Windows guide](WINDOWS.md).

## Background jobs and recovery

Long menu operations detach into the background. `Ctrl-C` leaves a foreground
view and returns to the menu; it does not stop detached installers or VMs.
The CLI `install` and `up` also detach by default. Use `--foreground` in automation
when you need the final exit code. A background command returning zero means its
worker was launched, **not** that the installation succeeded.

That worker is also what records the verdict: it watches the serial log for the
completion token and then sees QEMU exit. If it is killed — closing the menu it was
launched from is enough — both facts still happen and both stay on disk, but nothing
writes them down, and the lab then cannot tell the run from a failure. `recover VM`
reads them back afterwards. It cannot see whether QEMU left on its own, only that the
guest has stopped, so the verdict it writes says it was recovered rather than borrowing
the wording of one that was watched.

## Evidence and reports

To watch installation, choose **Use the VM → Follow screenshots (every 2 seconds)**
or run `./lab shot windows-11 --follow` (also available for Lubuntu). One browser
tab updates every two seconds, without sending guest input or retaining extra
frames on disk. Black frames show the serial log tail. Use `--interval 5` to
change the cadence, or `--no-open` to print the local viewer URL without opening
the browser. The terminal stays occupied until `Ctrl-C`, which ends the viewer
without stopping the VM or installer. The tab retains its last frame when the VM
stops or the viewer ends. Closing the tab stops requesting screenshots; use
`Ctrl-C` in the terminal to return to the menu. `--follow` cannot use `--nudge`.

Runtime files live under `work/PROFILE/`: `steps.log`, `serial.log`, `qemu.log`,
`qemu-command.txt`, `events.jsonl`, the disk, seed and screenshots. Earlier serial
logs are preserved under `logs/`. QMP operations share a bounded file lock; they
never race the recorder for the single-client socket. Black screenshots become
text frames containing the serial tail. Unchanged frames are deduplicated.

`report` produces `out/PROFILE.html` with embedded images, chronological commands,
durations, previous attempts, latest screen alongside the verdict, and a short
Italian user guide. Click any screenshot to enlarge it, adjust zoom, fit the
window or view it at 100%; close with `Esc`. The viewer works offline. Log tails
and serial text frames have terminal colors and control sequences removed for
readability; the original evidence files are preserved.
The same `screenshots/*.png` files can be selected for README
illustrations after a real run; no staged images are presented as installed VMs.
[out/example.html](../out/example.html) is a small, explicitly synthetic report example.
PDF dependencies (`markdown`, `weasyprint`, plus WeasyPrint's native libraries) are
loaded only on request. Missing dependencies leave HTML available.

## Cleanup and retries

```bash
./lab clean lubuntu-26.04 disk seed --dry-run
./lab clean lubuntu-26.04 disk seed       # lists paths, asks once, keeps logs/screens
./lab clean lubuntu-26.04 screenshots logs
./lab clean lubuntu-26.04 all             # excludes ISO AND shared SSH keys
./lab clean lubuntu-26.04 iso             # separate, explicit expensive-media choice
./lab clean lubuntu-26.04 keys            # shared by both guests; use deliberately
```

An attempted installation disk is never reused automatically. Stop the VM and
review its evidence before selecting `clean disk seed` to retry. `--keep-failed`
is always active; there is no unattended destructive retry. Cleanup is per path,
rejects managed symlinks and blocks while the VM or its lifecycle worker is active.
Cleanup itself leaves an audit event. Reports can contain output of commands you
ran inside a guest; review them before sharing.
