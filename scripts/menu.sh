#!/usr/bin/env bash
set -Eeuo pipefail
ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
export ROOT
if ! command -v fzf >/dev/null; then
    printf 'fzf is missing. Run: %q doctor --install\n' "$ROOT/lab"
    exit 1
fi
if [[ ! -t 0 || ! -t 1 ]]; then
    printf 'The menu requires a terminal. Use ./lab --help or ./lab status.\n' >&2
    exit 1
fi
# Python validates managed paths before Bash opens any local logs.
"$ROOT/lab" status >/dev/null
mkdir -p "$ROOT/work/ubuntu-26.04" "$ROOT/work/windows-11"
touch "$ROOT/work/menu.log" "$ROOT/work/ubuntu-26.04/steps.log" "$ROOT/work/windows-11/steps.log"
if command -v tmux >/dev/null && [[ -z ${TMUX:-} && -z ${LAB_NO_TMUX:-} ]]; then
    printf -v launch 'bash %q' "$ROOT/scripts/menu.sh"
    exec tmux new-session -s "qemu-playground-$$" "$launch"
fi
pane=''
if [[ -n ${TMUX:-} ]]; then
    printf -v follow 'tail -n 20 -F %q %q %q' "$ROOT/work/menu.log" "$ROOT/work/ubuntu-26.04/steps.log" "$ROOT/work/windows-11/steps.log"
    pane="$(tmux split-window -d -h -p 45 -P -F '#{pane_id}' "$follow")"
fi
cleanup() {
    if [[ -n $pane ]]; then tmux kill-pane -t "$pane" 2>/dev/null || true; fi
}
trap cleanup EXIT
trap ':' INT
profile=ubuntu-26.04
while true; do
    printf '\nProfile: %s · Ctrl-P switch · Ctrl-R refresh · Esc exit\n' "$profile"
    export LAB_MENU_PROFILE="$profile"
    # shellcheck disable=SC2016
    preview='"$ROOT/lab" _preview "$LAB_MENU_PROFILE" {1}; timeout 300 tail -n 20 -F "$ROOT/work/$LAB_MENU_PROFILE/steps.log"'
    selection=''
    selection="$("$ROOT/lab" _menu "$profile" | fzf --delimiter=$'\t' --with-nth=2.. \
        --prompt='lab > ' --header="${profile} — commands and live log in preview" \
        --expect=ctrl-p,ctrl-r --preview="$preview" --preview-window='right:55%:wrap' --height=90%)" || {
        code=$?
        if [[ $code -eq 130 ]]; then
            # fzf reports Esc and Ctrl-C alike; return to the profile/menu chooser.
            choice="$(printf 'Back to menu\nExit\n' | fzf --prompt='lab > ' --height=8)" || break
            [[ $choice == Exit ]] && break
        fi
        continue
    }
    key="${selection%%$'\n'*}"
    if [[ $key == ctrl-p ]]; then
        if [[ $profile == ubuntu-26.04 ]]; then profile=windows-11; else profile=ubuntu-26.04; fi
        continue
    fi
    [[ $key == ctrl-r ]] && continue
    row="${selection#*$'\n'}"
    item="${row%%$'\t'*}"
    [[ $item =~ ^[0-9]+$ ]] || continue
    "$ROOT/lab" _execute "$profile" "$item" 2>&1 | tee -a "$ROOT/work/menu.log" || true
    printf '\nPress Enter to return to the menu. '
    read -r _ || true
 done
