# Pinned media provenance

Checked on **2026-09-16**. The expected values are versioned in `profiles/*.json`.
Only compare downloaded media with those values; never replace a value merely
because a local file hashes differently.

| Profile | Vendor artifact / table entry | SHA-256 source |
| --- | --- | --- |
| lubuntu-26.04 | `ubuntu-26.04-live-server-amd64.iso` (26.04 GA bootstrap medium; installs required `lubuntu-desktop` packages) | https://releases.ubuntu.com/26.04/SHA256SUMS |
| windows-11 | Microsoft Windows 11 download page, `Italian 64-bit` — 25H2 media, distributed filename `Win11_25H2_Italian_x64_v2.iso`, ISO volume id `CCCOMA_X64FRE_IT-IT_DV9`, 7986 MiB | https://www.microsoft.com/en-us/software-download/windows11 |

Lubuntu's Ubuntu bootstrap medium SHA-256:

```text
dec49008a71f6098d0bcfc822021f4d042d5f2db279e4d75bdd981304f1ca5d9
```

Windows Italian x64 SHA-256:

```text
e72cba0335c8e67ac523f3f997f6d38e441eb06414ce42fd38b08a639abd0d87
```

The Windows table is mutable and its download links expire after 24 hours. The
local filename `windows-11-italian-x64.iso` is deliberately independent of the
vendor download filename. If Microsoft serves a newer image, it will be rejected
until a reviewed profile update records the new vendor checksum and check date.
A mismatch is not permission to trust the file or silently update the manifest.

The Windows pin was confirmed on 2026-09-16 against a local copy of that exact
vendor image. Microsoft rotates the build monthly and publishes a different hash
per build and per language, so a newer download will be rejected until a reviewed
profile update records the new vendor checksum and check date. That rejection is
the intended behaviour, not a defect.

The image is UDF bridge media (`BEA01`/`NSR02`) and `sources/install.wim` is
6.95 GiB, but the prompt-free rebuild is plain ISO-9660 level 3 and Windows reads
the multi-extent file from it; see `DECISIONS.md`.

No universal QGA MSI checksum is invented here. Choose a signed x64 installer
from its distributor, record the expected distributor checksum and source in
`LAB_QGA_SHA256` / `LAB_QGA_SOURCE`, and set `LAB_QGA_MSI` to the local file. These
values live in ignored `.env`; they are an explicit local trust input, unlike the
versioned ISO pins. Preparation verifies its digest, and the guest re-verifies the
same digest before executing it. Authenticode status is recorded on the serial
log but is not required: the virtio-win installers are distributed unsigned.
See `WINDOWS.md` and `DECISIONS.md`.

The status menu uses a verification cache bound to the ISO's device, inode, size,
mtime, ctime and expected digest. A changed file becomes unverified. `iso verify`
and installation preflight always hash the full file again. The cache accelerates
UI refreshes; it is not a replacement for full verification before installation.
