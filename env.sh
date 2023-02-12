#!/bin/bash
declare -A ISO

ISO["ubuntu"]="https://releases.ubuntu.com/jammy/ubuntu-22.04.1-desktop-amd64.iso"
ISO["puppy"]="https://distro.ibiblio.org/puppylinux/puppy-fossa/fossapup64-9.5.iso"
ISO["mx-linux"]="https://nav.dl.sourceforge.net/project/mx-linux/Final/Xfce/MX-21.3_x64.iso"
ISO["manjaro"]="https://download.manjaro.org/kde/22.0.2/manjaro-kde-22.0.2-230203-linux61.iso"
ISO["slax"]="https://ftp.linux.cz/pub/linux/slax/Slax-11.x/slax-64bit-11.6.0.iso"


HDD_PATH=hdd
MNT_PATH=mnt
OPT_PATH=opt
ISO_PATH=iso
DOWNLOAD_PATH=download
