#!/bin/sh
set -eu

RULE='SUBSYSTEM=="usb", ENV{DEVTYPE}=="usb_device", ATTR{idVendor}=="1d50", ATTR{idProduct}=="606f", MODE="0660", GROUP="plugdev", TAG+="uaccess"'
POWER='ACTION=="add", SUBSYSTEM=="usb", ENV{DEVTYPE}=="usb_device", ATTR{idVendor}=="1d50", ATTR{idProduct}=="606f", TEST=="power/control", ATTR{power/control}="on"'
printf '%s\n' "$RULE" "$POWER" | sudo tee /etc/udev/rules.d/70-mono-candlelight.rules >/dev/null
sudo udevadm control --reload-rules
sudo udevadm trigger --action=add --subsystem-match=usb --attr-match=idVendor=1d50 --attr-match=idProduct=606f
sudo udevadm settle --timeout=5
printf '%s\n' 'candleLight USB CAN 권한 및 절전 해제 규칙 적용 완료. 재연결 시에도 적용됩니다.'
