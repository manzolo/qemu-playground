#!/bin/bash

. $(dirname $BASH_SOURCE)/../env.sh

#for iso in "${!ISO[@]}"; do
#  echo "${iso} => ${ISO[${iso}]}"
#done

NC=$'\033[0m' # No Color

function msg_info() {
  local GREEN=$'\033[0;32m'
  printf "%s\n" "${GREEN}${*}${NC}" >&2
}

function msg_warn() {
  local BROWN=$'\033[0;33m'
  printf "%s\n" "${BROWN}${*}${NC}" >&2
}

function msg_error() {
  local RED=$'\033[0;31m'
  printf "%s\n" "${RED}${*}${NC}" >&2
}

function msg_fatal() {
  msg_error "${*}"
  exit 1
}

check_command_exists() {
    if ! command -v $1 &> /dev/null
    then
        msg_error "$1 could not be found!"
        exit 1
    fi
}

build_env() {
    mkdir -p $HDD_PATH
    mkdir -p $MNT_PATH
    mkdir -p $OPT_PATH
    mkdir -p $ISO_PATH
    mkdir -p $DOWNLOAD_PATH
}
