"""Read-only checks for the installed Lubuntu desktop, also used after boot."""


def lubuntu_check(*, running=False):
    checks = [
        "test \"$(dpkg-query -W -f='${Status}' lubuntu-desktop)\" = 'install ok installed'",
        'command -v lxqt-session >/dev/null',
        'test -s /usr/share/xsessions/Lubuntu.desktop',
        'test "$(systemctl get-default)" = graphical.target',
        'systemctl is-enabled --quiet sddm.service',
    ]
    if running:
        checks.append('systemctl is-active --quiet display-manager.service')
    return ' && '.join(checks)
