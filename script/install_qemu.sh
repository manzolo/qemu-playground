#! /bin/bash
. $(dirname $BASH_SOURCE)/__functions.sh
msg_info "Install QEMU"
sudo apt update && sudo apt upgrade
sudo apt install libvirt-daemon
sudo systemctl enable libvirtd
sudo systemctl start libvirtd
sudo apt install qemu-kvm qemu-system-arm