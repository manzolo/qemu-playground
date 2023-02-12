#!/bin/bash
#. $(dirname $BASH_SOURCE)/../env.sh
. $(dirname $BASH_SOURCE)/../script/__functions.sh
ZIP_FILE=raspbian
IMG_FILE=2017-04-10-raspbian-jessie
RASPBIAN_URL=http://downloads.raspberrypi.org/raspbian/images/raspbian-2017-04-10/$IMG_FILE.zip
KERNEL_URL=https://github.com/dhruvvyas90/qemu-rpi-kernel/raw/master/kernel-qemu-4.4.34-jessie
KERNEL_FILE=kernel-qemu-4.4.34-jessie

# Check Qemu
msg_warn "Check Qemu..."
check_command_exists "qemu-system-arm"


if test -f "$HDD_PATH/$IMG_FILE.img"; then
    echo "$HDD_PATH/$IMG_FILE.img exists, skip download..."
else
    wget -O $DOWNLOAD_PATH/$ZIP_FILE.zip $RASPBIAN_URL
    unzip $DOWNLOAD_PATH/$ZIP_FILE.zip -d $HDD_PATH
    rm $DOWNLOAD_PATH/$ZIP_FILE.zip
fi

if test -f "$OPT_PATH/$KERNEL_FILE"; then
    echo "$OPT_PATH/$KERNEL_FILE exists, skip download..."
else
    wget -O $OPT_PATH/$KERNEL_FILE $KERNEL_URL
fi

#fdisk -l $IMG_FILE.img

START_OFFSET=`fdisk -l $HDD_PATH/$IMG_FILE.img | grep $IMG_FILE.img2 | awk '{print $2}'`
OFFSET=$(($START_OFFSET * 512))
mkdir -p $MNT_PATH/
sudo umount $MNT_PATH 2>&1 > /dev/null
sleep 2
sudo mount -v -o offset=$OFFSET -t ext4 $HDD_PATH/$IMG_FILE.img $MNT_PATH

qemu-system-arm -kernel $OPT_PATH/$KERNEL_FILE \
                -cpu arm1176 \
                -m 256 \
                -M versatilepb \
                -serial stdio \
                -append "root=/dev/sda2 rootfstype=ext4 rw" \
                -hda $HDD_PATH/$IMG_FILE.img \
                -nic user,hostfwd=tcp::5022-:22 -no-reboot

sudo umount $MNT_PATH
