#! /bin/bash
clear

. $(dirname $0)/script/__functions.sh 
. $(dirname $0)/include/__wait.sh
. $(dirname $0)/include/__show_info.sh
. $(dirname $0)/include/__create_vhd.sh
. $(dirname $0)/include/__try_os.sh

build_env

while [ 1 ]
do
CHOICE=$(
whiptail --title "QEMU playground" \
					  --ok-button "Select" \
                      --nocancel \
					  --menu "Make your choice" 0 0 0 \
	"1)" "Show info"   \
	"2)" "Install QEMU"   \
	"3)" "Try Raspbian"  \
	"4)" "Try Ubuntu" \
	"5)" "Try Puppy" \
	"6)" "Try MX Linux" \
	"7)" "Try Manjaro" \
	"8)" "Try Slax" \
	"Q)" "Exit"  3>&2 2>&1 1>&3	
)
case $CHOICE in
    "1)")
		show_info
		do_waitkey
    ;;
	"2)")   
		$(dirname $0)/script/install_qemu.sh
		do_waitkey
	;;
	"3)")   
		$(dirname $0)/raspbian/emulate_raspbian.sh
		do_waitkey
	;;
	"4)")
		try_os "ubuntu"
		do_waitkey
	;;
	"5)")
		try_os "puppy"
		do_waitkey
	;;
	"6)")
		try_os "mx-linux"
		do_waitkey
	;;
	"7)")
		try_os "manjaro"
		do_waitkey
	;;
	"8)")
		try_os "slax"
		do_waitkey
	;;
	"Q)")   
		exit
	;;
esac
done
