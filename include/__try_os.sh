function try_os()
{
. $(dirname $BASH_SOURCE)/../script/__functions.sh
    local OS=$1

    # Check Qemu
    msg_warn "Check Qemu..."
    check_command_exists "qemu-system-x86_64"

    ISO_FILE=${ISO[$OS]##*/}
    URL=${ISO[$OS]}
    HD_FILE="$OS.qcow2"
    if test -f "$ISO_PATH/$ISO_FILE"; then
        msg_warn "$ISO_FILE exists, skip download..."
    else
        msg_warn "$ISO_FILE download..."
        wget -O $ISO_PATH/$ISO_FILE $URL
    fi
    if test -f ${HDD_PATH}/${HD_FILE}; then
        msg_warn "$HD_FILE exists, skip hd creation..."
    else
        msg_warn "create virtual hdd"
        qemu-img create -f qcow2 ${HDD_PATH}/${HD_FILE} 16G
    fi
    qemu-system-x86_64 -M pc \
                       -enable-kvm -boot d \
                       -cpu host \
                       -m 4096 \
                       -drive format=qcow2,file=$HDD_PATH/${HD_FILE},if=none,id=drv0 \
                       -cdrom $ISO_PATH/$ISO_FILE \
                       -device virtio-net-pci,netdev=net0,romfile="" \
                       -netdev type=user,id=net0 \
                       -device virtio-blk-pci,drive=drv0 \
                       -object rng-random,filename=/dev/urandom,id=rng0 \
                       -device virtio-rng-pci,rng=rng0 

                        

}
