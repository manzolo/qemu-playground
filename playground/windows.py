"""Windows bootstrap postconditions, shared with the host's cold-boot check."""
import base64

QGA_EXE = r'C:\Program Files\qemu-ga\qemu-ga.exe'
WINLOGON = r'HKLM:\SOFTWARE\Microsoft\Windows NT\CurrentVersion\Winlogon'
UNATTEND = (r'C:\Windows\Panther\unattend.xml',
            r'C:\Windows\Panther\Unattend\unattend.xml')


def windows_check(cfg, *, running=False):
    """Throw on any unmet postcondition; never change the guest to make it pass.

    Unlike Lubuntu's in-target check, the first-logon bootstrap already runs in
    live Windows. Both modes therefore require running services. cfg and running
    keep the same interface as lubuntu_check; these guarantees are unconditional.
    Only a host-side QGA ping proves the channel works, even with these checks green.
    """
    paths = ', '.join("'" + path + "'" for path in UNATTEND)
    return '\n'.join([
        "$ErrorActionPreference = 'Stop'",
        "foreach ($name in 'sshd', 'QEMU-GA') {",
        '    $service = @(Get-CimInstance Win32_Service -Filter "Name=\'$name\'")',
        "    if ($service.Count -ne 1 -or $service[0].State -ne 'Running' -or $service[0].StartMode -ne 'Auto') {",
        '        throw "$name must be Running with automatic startup"',
        '    }',
        '}',
        f"if (!(Test-Path -LiteralPath '{QGA_EXE}' -PathType Leaf)) {{ throw 'qemu-ga.exe missing' }}",
        "$rule = @(Get-NetFirewallRule -PolicyStore ActiveStore -Name 'qemu-playground-sshd')",
        "if ($rule.Count -ne 1 -or $rule[0].Enabled -ne 'True' -or $rule[0].Direction -ne 'Inbound' -or $rule[0].Action -ne 'Allow' -or $rule[0].Profile -ne 'Any') {",
        "    throw 'qemu-playground-sshd must allow inbound traffic on every profile in ActiveStore'",
        '}',
        '$port = @($rule | Get-NetFirewallPortFilter)',
        "if ($port.Count -ne 1 -or $port[0].Protocol -notin @('TCP', '6') -or @($port[0].LocalPort).Count -ne 1 -or $port[0].LocalPort -ne '22') {",
        "    throw 'qemu-playground-sshd must allow TCP port 22'",
        '}',
        f"$key = Get-Item -LiteralPath '{WINLOGON}'",
        "$names = @($key.GetValueNames())",
        "if ($names -contains 'AutoAdminLogon' -and $key.GetValue('AutoAdminLogon') -ne '0') { throw 'AutoAdminLogon is not disabled' }",
        "if ($names -contains 'DefaultPassword') { throw 'DefaultPassword still exists' }",
        f'foreach ($path in {paths}) {{',
        '    if (Test-Path -LiteralPath $path) { throw "Cached answer file still exists: $path" }',
        '}',
    ])


def windows_check_command(cfg):
    """Transport the same check through cmd.exe without another quoting layer.

    Explicit exits keep a failed condition from being masked by later output.
    The bootstrap instead lets the exception reach its existing LAB_FAIL handler.
    """
    script = ('try {\n' + windows_check(cfg, running=True) + '\n} catch {\n'
              '    [Console]::Error.WriteLine($_.Exception.Message)\n'
              '    exit 1\n}\nexit 0\n')
    encoded = base64.b64encode(script.encode('utf-16le')).decode('ascii')
    return 'powershell.exe -NoProfile -NonInteractive -EncodedCommand ' + encoded
