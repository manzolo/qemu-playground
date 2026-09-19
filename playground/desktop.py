"""What the seed writes for the installed Lubuntu desktop, and how it is verified.

The installer and the host share these definitions deliberately. A check only the
seed knew would let the two drift apart, and the one thing this profile promises -
that you arrive at a usable graphical login - would again be something only the
installer had an opinion about.
"""

SDDM_CONF = '/etc/sddm.conf.d/90-lab.conf'
XSETUP = '/etc/sddm/lab-xsetup'
SESSION = 'Lubuntu.desktop'
SESSION_CONF = '/etc/xdg/lxqt/session.conf'
AUTOSTART = '/etc/xdg/autostart/qemu-playground-noblank.desktop'
NOBLANK = 'xset s off -dpms'


def session_conf(cfg):
    """LXQt sets these in its own process, which is the only layer that wins here.

    The installed desktop came up in English under it_IT.UTF-8 because the session
    ran with LANG=C.UTF-8 - systemd's manager environment, which is what reaches the
    session and beats pam_env. /etc/default/locale, /etc/locale.conf and
    /etc/environment were all correct and all lost, the last of those tried and
    measured. Restarting SDDM by hand hid the whole thing, because only a cold boot
    puts systemd's value in the way; the reinstall is what caught it.
    """
    return f'[Environment]\nLANG={cfg["LAB_LOCALE"]}\n'


def noblank_desktop():
    """Passive screenshots are how this lab watches a guest it refuses to type into,
    and X blanks the screen after ten minutes: `shot` came back with a black
    framebuffer and the serial tail instead of a desktop. Windows was given the same
    treatment on 2026-09-16 for the same reason. Waking it with a keystroke would
    mark an otherwise unattended run assisted, so the fix belongs in the guest."""
    return ('[Desktop Entry]\nType=Application\nName=qemu-playground: keep the screen watchable\n'
            f'Exec=sh -c "{NOBLANK}"\nOnlyShowIn=LXQt;\nNoDisplay=true\nX-GNOME-Autostart-enabled=true\n')


def xsetup_script(cfg):
    """SDDM starts its greeter on an X server of its own, which does not read
    /etc/default/keyboard. Without this the greeter offers us while the installed
    system is configured otherwise - and the console password is typed there, on
    a layout where - and several other characters have moved."""
    return ('#!/bin/sh\n'
            '# Written by qemu-playground: the greeter must use LAB_KEYBOARD.\n'
            '[ -x /usr/share/sddm/scripts/Xsetup ] && /usr/share/sddm/scripts/Xsetup "$@"\n'
            f'exec setxkbmap -model pc105 -layout {cfg["LAB_KEYBOARD"]}\n')


def sddm_conf(cfg):
    """A drop-in, so the distribution's own lubuntu_settings.conf stays untouched."""
    blocks = []
    if cfg['LAB_AUTOLOGIN'] == '1':
        # Relogin=false: logging out returns to the greeter rather than looping
        # straight back into a session nobody asked for.
        blocks.append(f'[Autologin]\nUser={cfg["LAB_USER"]}\n'
                      f'Session={SESSION}\nRelogin=false\n')
    blocks.append(f'[X11]\nDisplayCommand={XSETUP}\n')
    return '\n'.join(blocks)


def environ(cfg, process='lxqt-session'):
    """A live process's environment, which is where these settings are either true
    or merely written down in a file somewhere.

    Which process matters. `/proc/<pid>/environ` is what a process was *started*
    with, not what it holds now, and lxqt-session applies its `[Environment]` block
    by setting variables for the programs it then launches. Reading `LANG` off
    lxqt-session itself therefore reports whatever SDDM handed it - nothing, or
    `C.UTF-8` - however well the session is configured, and it once agreed with the
    file by coincidence. A child LXQt started carries the applied value. `DISPLAY`
    and `XAUTHORITY` are the opposite case: SDDM sets those before exec, so they are
    in lxqt-session's own environment and nowhere else this early.
    """
    return f'/proc/"$(pgrep -u {cfg["LAB_USER"]} -x {process} | head -1)"/environ'


def lubuntu_check(cfg, *, running=False):
    """One definition of "the desktop is there", run as a late command before the
    completion token and again over SSH against the booted guest."""
    checks = [
        "test \"$(dpkg-query -W -f='${Status}' lubuntu-desktop)\" = 'install ok installed'",
        'command -v lxqt-session >/dev/null',
        'test -s /usr/share/xsessions/' + SESSION,
        'test "$(systemctl get-default)" = graphical.target',
        'systemctl is-enabled --quiet sddm.service',
        # The greeter's layout is applied by a script that calls setxkbmap, so a
        # missing setxkbmap would leave the greeter silently on us.
        'command -v setxkbmap >/dev/null',
        f'test -x {XSETUP}',
        f'grep -qx "exec setxkbmap -model pc105 -layout {cfg["LAB_KEYBOARD"]}" {XSETUP}',
        f'grep -qx "DisplayCommand={XSETUP}" {SDDM_CONF}',
        f'grep -qx "LANG={cfg["LAB_LOCALE"]}" {SESSION_CONF}',
        # xset applies it; a missing one would blank the screen in silence.
        'command -v xset >/dev/null',
        f'grep -q "{NOBLANK}" {AUTOSTART}',
    ]
    if cfg['LAB_AUTOLOGIN'] == '1':
        checks.append(f'grep -qx "User={cfg["LAB_USER"]}" {SDDM_CONF}')
        checks.append(f'grep -qx "Session={SESSION}" {SDDM_CONF}')
    if running:
        checks.append('systemctl is-active --quiet display-manager.service')
        if cfg['LAB_AUTOLOGIN'] == '1':
            # Not merely "a session for the user exists": SSH makes one of those on
            # every check. The automatic one is the active session on seat0.
            checks.append('test "$(loginctl show-session '
                          '"$(loginctl show-seat seat0 -p ActiveSession --value)" '
                          f'-p Name --value)" = {cfg["LAB_USER"]}')
            # The file being right is not the session having read it: a correct
            # locale in three system files still lost to systemd's C.UTF-8, and
            # only the session's own environment says which one won. environ is
            # NUL-separated, hence -z.
            checks.append(f'grep -qz "^LANG={cfg["LAB_LOCALE"]}$" '
                          + environ(cfg, 'lxqt-panel'))
            # Same rule for blanking: the autostart entry existing is not the X
            # server having acted on it, and only the X server can say.
            checks.append(f'env $(tr "\\0" "\\n" < {environ(cfg)} '
                          '| grep -E "^DISPLAY=|^XAUTHORITY=" | tr "\\n" " ") '
                          'xset -q | grep -q "DPMS is Disabled"')
    return ' && '.join(checks)
