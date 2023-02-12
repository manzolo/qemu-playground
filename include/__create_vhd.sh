function create_vhd()
{
FILENAME=$(whiptail --inputbox "Enter file name" 10 30 3>&1 1>&2 2>&3)
GB=$(whiptail --inputbox "Enter GB" 10 30 3>&1 1>&2 2>&3)
qemu-img create hdd/${FILENAME} ${GB}G
return $FILENAME
}