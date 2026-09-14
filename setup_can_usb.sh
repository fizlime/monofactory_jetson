#!/bin/sh
set -eu

RULE='SUBSYSTEM=="usb", ATTR{idVendor}=="1d50", ATTR{idProduct}=="606f", MODE="0660", GROUP="plugdev", TAG+="uaccess"'
printf '%s\n' "$RULE" | sudo tee /etc/udev/rules.d/70-mono-candlelight.rules >/dev/null
sudo udevadm control --reload-rules
sudo udevadm trigger
printf '%s\n' 'candleLight USB CAN을 분리한 뒤 다시 연결하세요.'
