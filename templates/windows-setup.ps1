# Executed once by the local administrator created by the answer file.
$ErrorActionPreference = 'Stop'
# Runs from the seed CD at first logon, elevated. Inputs come from $PSScriptRoot
# (the CD); only logs are written to the local disk.
$seed = $PSScriptRoot
$base = 'C:\ProgramData\QemuPlayground'
New-Item $base -ItemType Directory -Force | Out-Null
Start-Transcript -Path "$base\setup.log" -Append
$serial = New-Object System.IO.Ports.SerialPort 'COM1',115200,'None',8,'One'
$serial.Open()
function Emit([string]$message) {
    $serial.WriteLine($message)
    $serial.BaseStream.Flush()
    Write-Host $message
}
# Never discard a native command's output: it is the only explanation of its exit code.
function Native([string]$what, [scriptblock]$block) {
    $global:LASTEXITCODE = 0
    $out = & $block 2>&1
    if ($LASTEXITCODE) { throw ($what + " failed (exit $LASTEXITCODE): " + (($out | Out-String).Trim())) }
    return $out
}
try {
    # First of all, stop the guest blanking its own screen: the passive timeline is
    # the only window on a long bootstrap, and a black rectangle documents nothing.
    foreach ($t in 'monitor-timeout-ac', 'monitor-timeout-dc', 'standby-timeout-ac',
                   'standby-timeout-dc', 'hibernate-timeout-ac', 'hibernate-timeout-dc') {
        Native "powercfg /change $t" { powercfg.exe /change $t 0 } | Out-Null
    }
    # Features on Demand fails while Windows Update is still waking up; retry.
    $cap = Get-WindowsCapability -Online -Name 'OpenSSH.Server~~~~0.0.1.0'
    for ($try = 1; $cap.State -ne 'Installed' -and $try -le 10; $try++) {
        try { Add-WindowsCapability -Online -Name 'OpenSSH.Server~~~~0.0.1.0' | Out-Null }
        catch { Emit "OpenSSH capability attempt $try failed: $($_.Exception.Message)"; Start-Sleep 30 }
        $cap = Get-WindowsCapability -Online -Name 'OpenSSH.Server~~~~0.0.1.0'
    }
    if ($cap.State -ne 'Installed') { throw 'OpenSSH Server capability could not be installed' }
    $key = [Text.Encoding]::UTF8.GetString([Convert]::FromBase64String('@PUBLIC_KEY_B64@'))
    New-Item "$env:ProgramData\ssh" -ItemType Directory -Force | Out-Null
    $keyPath = "$env:ProgramData\ssh\administrators_authorized_keys"
    [IO.File]::WriteAllText($keyPath, "$key`n", [Text.Encoding]::ASCII)
    Native 'icacls on administrators_authorized_keys' {
        icacls.exe $keyPath /inheritance:r /grant '*S-1-5-18:F' '*S-1-5-32-544:F'
    } | Out-Null
    Set-Service sshd -StartupType Automatic
    Start-Service sshd
    $config = "$env:ProgramData\ssh\sshd_config"
    $text = [IO.File]::ReadAllText($config)
    # Place global settings before Match blocks, removing any conflicting global defaults.
    $text = $text -replace '(?m)^\s*#?\s*PasswordAuthentication\s+.*$', ''
    $text = $text -replace '(?m)^\s*#?\s*PubkeyAuthentication\s+.*$', ''
    [IO.File]::WriteAllText($config, "PasswordAuthentication no`nPubkeyAuthentication yes`n$text")
    # Our own rule, always enabled and on every profile. The capability's
    # OpenSSH-Server-In-TCP may be missing, disabled, or scoped to a profile the
    # QEMU user network is not classified in, which shows up as an SSH connection
    # that hangs during banner exchange rather than as a refusal.
    if (!(Get-NetFirewallRule -Name 'qemu-playground-sshd' -ErrorAction SilentlyContinue)) {
        New-NetFirewallRule -Name 'qemu-playground-sshd' -DisplayName 'OpenSSH Server (qemu-playground)' `
            -Enabled True -Profile Any -Direction Inbound -Protocol TCP -LocalPort 22 -Action Allow | Out-Null
    }
    New-ItemProperty 'HKLM:\SOFTWARE\OpenSSH' -Name DefaultShell -Value 'C:\Windows\System32\cmd.exe' -PropertyType String -Force | Out-Null
    Restart-Service sshd
    $msi = "$seed\qemu-ga-x64.msi"
    # The host pinned and verified this digest before staging the file; re-verify it
    # here so media corruption cannot go unnoticed. Authenticode is recorded, not
    # required: the virtio-win installers are distributed unsigned (see DECISIONS.md).
    $expected = '@QGA_SHA256@'
    $actual = (Get-FileHash -Algorithm SHA256 $msi).Hash.ToLower()
    if ($expected -notmatch '^[0-9a-f]{64}$') { throw 'No pinned QGA MSI digest was rendered into this seed' }
    if ($actual -ne $expected) { throw "QGA MSI SHA-256 mismatch: got $actual, expected $expected" }
    Emit "QGA MSI matches pinned SHA-256; Authenticode status: $((Get-AuthenticodeSignature $msi).Status)"
    $install = Start-Process msiexec.exe -ArgumentList "/i `"$msi`" /qn /norestart /l*v `"$base\qga-install.log`"" -PassThru
    if (!$install.WaitForExit(600000)) { throw 'Timeout waiting for QGA MSI installer (600s)' }
    if ($install.ExitCode -notin @(0,3010)) { throw "QGA MSI exit $($install.ExitCode)" }
    $exe = 'C:\Program Files\qemu-ga\qemu-ga.exe'
    if (!(Test-Path $exe)) { throw 'qemu-ga.exe not found after MSI installation' }
    if (!(Get-Service QEMU-GA -ErrorAction SilentlyContinue)) { throw 'the MSI did not register the QEMU-GA service' }
    Stop-Service QEMU-GA -Force -ErrorAction SilentlyContinue
    # ImagePath is written directly instead of `sc.exe config binPath= "<quoted> args"`:
    # Windows PowerShell rebuilds native command lines and mangles the embedded quotes
    # around a path with spaces, so sc.exe rejected it (2026-09-16).
    $wanted = '"' + $exe + '" -d -m isa-serial -p COM2'
    Set-ItemProperty 'HKLM:\SYSTEM\CurrentControlSet\Services\QEMU-GA' -Name ImagePath -Value $wanted
    Set-Service QEMU-GA -StartupType Automatic
    Start-Service QEMU-GA
    if ((Get-Service QEMU-GA).Status -ne 'Running') { throw 'QEMU-GA did not start after reconfiguration' }
    Emit ('QEMU-GA running on COM2; sshd is ' + (Get-Service sshd).Status +
          '; firewall rules for port 22: ' + ((Get-NetFirewallRule -Direction Inbound -Enabled True |
          Where-Object { ($_ | Get-NetFirewallPortFilter).LocalPort -eq 22 }).Name -join ','))
    # Remove autologon credentials, including the cached answer files.
    $winlogon = 'HKLM:\SOFTWARE\Microsoft\Windows NT\CurrentVersion\Winlogon'
    Set-ItemProperty $winlogon AutoAdminLogon '0'
    Remove-ItemProperty $winlogon DefaultPassword -ErrorAction SilentlyContinue
    Remove-Item 'C:\Windows\Panther\unattend.xml' -ErrorAction SilentlyContinue
    Remove-Item 'C:\Windows\Panther\Unattend\unattend.xml' -ErrorAction SilentlyContinue
    Stop-Transcript
    # Flush filesystem and storage cache BEFORE emitting completion. Never freeze
    # volumes via QGA just to implement a completion marker.
    Write-VolumeCache -DriveLetter C -ErrorAction Stop
    Emit 'LAB_OK_@TOKEN@'
    $serial.Close()
    & shutdown.exe /s /t 0
} catch {
    Emit ("SETUP ERROR: " + $_.Exception.Message)
    Emit 'LAB_FAIL_@TOKEN@'
    Stop-Transcript -ErrorAction SilentlyContinue
    $serial.Close()
    exit 1
}
