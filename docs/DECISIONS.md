# Decisions and evidence

| Date | Decision | Evidence / reason |
| --- | --- | --- |
| 2026-09-16 | Keep the core on Python 3.10+ standard library; use Bash only for entrypoint/menu. | Seed/protocol/report behavior is tested without a VM; no mandatory pip bootstrap. |
| 2026-09-16 | Pin Ubuntu 26.04 GA, not the mutable latest point release. | Canonical's SHA256SUMS publishes both GA and 26.04.1; a moving URL would defeat reproducibility. |
| 2026-09-16 | Pin the Microsoft Italian x64 checksum, import media manually. | Vendor download links expire; the public checksum table was checked on this date. |
| 2026-09-16 | Require an explicit local QGA MSI trust input plus Authenticode verification. | There is no verified, universal distributor MSI checksum to invent; missing inputs visibly block Windows preparation. |
| 2026-09-16 | Use SATA/e1000e and QGA on COM2 for Windows. | QEMU's channel-win32.c supports isa-serial; this removes the extra virtio driver ISO from the minimal flow. |
| 2026-09-16 | Build Windows boot media from the vendor's efisys_noprompt.bin. | Avoids the DVD key prompt without disguising an assisted install as unattended. Missing image is a hard diagnostic. |
| 2026-09-16 | Only explicit --nudge can send a key; record it before sending. | The original lab specification documents recorder-injected keys masking installer stalls. |
| 2026-09-16 | Dedicated QMP lock for every operation, plus bounded message handling. | Real QEMU smoke test issues six queries through concurrent clients, then captures a PNG. |
| 2026-09-16 | Require token and spontaneous QEMU exit; latch token observation. | Regression tests ensure the host waits after the token and preserves disk/screens on a failure token. |
| 2026-09-16 | Distinguish disk existence, SSH reachability and installation evidence. | A qcow2 header is nonempty before an OS is installed; neither that nor a TCP port proves success. |
| 2026-09-16 | Retain attempted disks and refuse automatic reinstall. | Explicit cleanup is the only path to discarding an attempted disk; logs and timeline can survive retry. |
| 2026-09-16 | Verify executable, unique VM name, QMP path and disk path before signalling. | Test rejects the current Python process and a QEMU command with an unrelated QMP endpoint; force stop uses pidfd. |
| 2026-09-16 | ISO verification cache is only a UI optimization. | Device/inode/size/mtime/ctime changes invalidate it; prepare/install rehash against the versioned pin. |
| 2026-09-16 | Never evaluate .env as shell code. | Regression fixture with a literal command substitution neither executes nor leaks in config show. |
| 2026-09-16 | Reject symlinks and enumerate all selected paths before cleanup. | Regression test keeps both failed disk evidence and the symlink target intact. |
| 2026-09-16 | Do not infer OS-install success from firmware tests. | This development sandbox has no /dev/kvm and no matching ISO; real TCG tests cover firmware/QMP/TPM only. |
| 2026-09-16 | Use a streaming SHA implementation compatible with Python 3.10. | hashlib.file_digest was added later; the CI matrix includes 3.10. |
| 2026-09-16 | Recheck the SSH forwarding endpoint against the running QEMU command. | Editing the port in .env while a VM runs must not redirect a guest command to another local service. |
