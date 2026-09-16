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
touch "$ROOT/work/ubuntu-26.04/steps.log" "$ROOT/work/windows-11/steps.log"
if command -v tmux >/dev/null && [[ -z ${TMUX:-} && -z ${LAB_NO_TMUX:-} ]]; then
    printf -v launch 'bash %q' "$ROOT/scripts/menu.sh"
    exec tmux new-session -s "qemu-playground-$$" "$launch"
fi
pane=''
if [[ -n ${TMUX:-} ]]; then
    # Only the background workers' logs: menu.log would echo, line for line, what the
    # foreground command is already printing in the pane above.
    printf -v follow 'tail -n 20 -F %q %q' "$ROOT/work/ubuntu-26.04/steps.log" "$ROOT/work/windows-11/steps.log"
    # Split below, never beside: a side-by-side log pane halves the width fzf has
    # for labels, and the preview then halves what is left.
    pane="$(tmux split-window -d -v -p 32 -P -F '#{pane_id}' "$follow")"
fi
cleanup() {
    if [[ -n $pane ]]; then tmux kill-pane -t "$pane" 2>/dev/null || true; fi
}
trap cleanup EXIT
trap ':' INT
profile=ubuntu-26.04
while true; do
    export LAB_MENU_PROFILE="$profile"
    # With a tmux log pane the preview would only duplicate it; without tmux the
    # preview is the only place the log can scroll.
    # shellcheck disable=SC2016
    preview='"$ROOT/lab" _preview "$LAB_MENU_PROFILE" {1}'
    if [[ -z $pane ]]; then
        # shellcheck disable=SC2016
        preview+='; timeout 300 tail -n 20 -F "$ROOT/work/$LAB_MENU_PROFILE/steps.log"'
    fi
    selection=''
    # --layout=reverse keeps step 1 at the top; --height=100% stops the previous
    # header scrolling into the next draw; a narrower preview leaves the labels whole.
    selection="$("$ROOT/lab" _menu "$profile" | fzf --delimiter=$'\t' --with-nth=2.. \
        --prompt='lab > ' --layout=reverse --height=100% --border --info=inline \
        --header="$("$ROOT/lab" _header "$profile")" --header-first \
        --expect=ctrl-p,ctrl-r,ctrl-q,esc --preview="$preview" --preview-window='right:38%:wrap')" || {
        code=$?
        # Listing esc under --expect is what separates it from Ctrl-C: fzf reports both
        # as 130 otherwise, which is why this used to need an extra chooser. Now Esc
        # arrives as a named key (handled below) and 130 can only be Ctrl-C, which the
        # brief says must come back to the menu rather than end the lab.
        if [[ $code -eq 130 || $code -eq 1 ]]; then
            continue
        fi
        printf 'fzf exited with status %s; leaving the menu.\n' "$code" >&2
        break
    }
    key="${selection%%$'\n'*}"
    # This is the top level: there is nothing to go back to, so Esc leaves.
    if [[ $key == esc || $key == ctrl-q ]]; then
        break
    fi
    if [[ $key == ctrl-p ]]; then
        if [[ $profile == ubuntu-26.04 ]]; then profile=windows-11; else profile=ubuntu-26.04; fi
        continue
    fi
    [[ $key == ctrl-r ]] && continue
    row="${selection#*$'\n'}"
    item="${row%%$'\t'*}"
    [[ $item =~ ^[0-9]+$ ]] || continue
    # Start each command on a clean screen: otherwise runs pile on top of each other
    # and it stops being obvious which output belongs to what.
    clear
    # No pipe: piping through tee gave interactive entries a pipe for stdout, which
    # mangled an SSH session's screen handling and hid the confirmation prompts of
    # doctor --install and clean. Nothing read menu.log once the tmux pane stopped
    # tailing it, so the record it bought was worth less than what it cost.
    set +e
    "$ROOT/lab" _execute "$profile" "$item"
    outcome=$?
    set -e
    if [[ $outcome -eq 64 ]]; then
        break
    fi
    # Any single key, so Esc gets someone back to the menu just as Enter does.
    printf '\nPress any key to return to the menu. '
    read -rsn1 _ || true
    printf '\n'
 done
