# Pinned media provenance

Checked on **2026-09-16**. The expected values are versioned in `profiles/*.json`.
Only compare downloaded media with those values; never replace a value merely
because a local file hashes differently.

| Profile | Vendor artifact / table entry | SHA-256 source |
| --- | --- | --- |
| ubuntu-26.04 | `ubuntu-26.04-live-server-amd64.iso` (26.04 GA, not 26.04.1) | https://releases.ubuntu.com/26.04/SHA256SUMS |
| windows-11 | Microsoft Windows 11 download page, `Italian 64-bit` | https://www.microsoft.com/en-us/software-download/windows11 |

Ubuntu SHA-256:

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

No universal QGA MSI checksum is invented here. Choose a signed x64 installer
from its distributor, record the expected distributor checksum and source in
`LAB_QGA_SHA256` / `LAB_QGA_SOURCE`, and set `LAB_QGA_MSI` to the local file. These
values live in ignored `.env`; they are an explicit local trust input, unlike the
versioned ISO pins. Preparation verifies its digest, and the guest also verifies
its Authenticode signature before executing it. See `WINDOWS.md`.

The status menu uses a verification cache bound to the ISO's device, inode, size,
mtime, ctime and expected digest. A changed file becomes unverified. `iso verify`
and installation preflight always hash the full file again. The cache accelerates
UI refreshes; it is not a replacement for full verification before installation.
