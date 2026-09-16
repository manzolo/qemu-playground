# Executed once by the local administrator created by the answer file.
$ErrorActionPreference = 'Stop'
$base = 'C:\ProgramData\QemuPlayground'
Start-Transcript -Path "$base\setup.log" -Append
$serial = New-Object System.IO.Ports.SerialPort 'COM1',115200,'None',8,'One'
$serial.Open()
function Emit([string]$message) {
    $serial.WriteLine($message)
    $serial.BaseStream.Flush()
    Write-Host $message
}
try {
    $cap = Get-WindowsCapability -Online -Name 'OpenSSH.Server~~~~0.0.1.0'
    if ($cap.State -ne 'Installed') {
        Add-WindowsCapability -Online -Name 'OpenSSH.Server~~~~0.0.1.0' | Out-Null
    }
    $key = [Text.Encoding]::UTF8.GetString([Convert]::FromBase64String('@PUBLIC_KEY_B64@'))
    New-Item "$env:ProgramData\ssh" -ItemType Directory -Force | Out-Null
    $keyPath = "$env:ProgramData\ssh\administrators_authorized_keys"
    [IO.File]::WriteAllText($keyPath, "$key`n", [Text.Encoding]::ASCII)
    & icacls.exe $keyPath /inheritance:r /grant '*S-1-5-18:F' '*S-1-5-32-544:F' | Out-Null
    if ($LASTEXITCODE) { throw 'Unable to secure authorized_keys' }
    Set-Service sshd -StartupType Automatic
    Start-Service sshd
    $config = "$env:ProgramData\ssh\sshd_config"
    $text = [IO.File]::ReadAllText($config)
    # Place global settings before Match blocks, removing any conflicting global defaults.
    $text = $text -replace '(?m)^\s*#?\s*PasswordAuthentication\s+.*$', ''
    $text = $text -replace '(?m)^\s*#?\s*PubkeyAuthentication\s+.*$', ''
    [IO.File]::WriteAllText($config, "PasswordAuthentication no`nPubkeyAuthentication yes`n$text")
    if (!(Get-NetFirewallRule -Name 'OpenSSH-Server-In-TCP' -ErrorAction SilentlyContinue)) {
        New-NetFirewallRule -Name 'OpenSSH-Server-In-TCP' -Direction Inbound -Protocol TCP -LocalPort 22 -Action Allow | Out-Null
    }
    New-ItemProperty 'HKLM:\SOFTWARE\OpenSSH' -Name DefaultShell -Value 'C:\Windows\System32\cmd.exe' -PropertyType String -Force | Out-Null
    Restart-Service sshd
    $msi = "$base\qemu-ga-x64.msi"
    if ((Get-AuthenticodeSignature $msi).Status -ne 'Valid') { throw 'QGA MSI signature is not valid' }
    $install = Start-Process msiexec.exe -ArgumentList "/i `"$msi`" /qn /norestart /l*v `"$base\qga-install.log`"" -PassThru
    if (!$install.WaitForExit(600000)) { throw 'Timeout waiting for QGA MSI installer (600s)' }
    if ($install.ExitCode -notin @(0,3010)) { throw "QGA MSI exit $($install.ExitCode)" }
    Stop-Service QEMU-GA -ErrorAction SilentlyContinue
    $exe = 'C:\Program Files\qemu-ga\qemu-ga.exe'
    if (!(Test-Path $exe)) { throw 'qemu-ga.exe not found after MSI installation' }
    & sc.exe config QEMU-GA binPath= "`"$exe`" -d -m isa-serial -p COM2" start= auto | Out-Null
    if ($LASTEXITCODE) { throw 'QGA service configuration failed' }
    Start-Service QEMU-GA
    # Remove autologon credentials, including the cached answer files.
    $winlogon = 'HKLM:\SOFTWARE\Microsoft\Windows NT\CurrentVersion\Winlogon'
    Set-ItemProperty $winlogon AutoAdminLogon '0'
    Remove-ItemProperty $winlogon DefaultPassword -ErrorAction SilentlyContinue
    Remove-Item "$base\autounattend.xml" -ErrorAction SilentlyContinue
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
