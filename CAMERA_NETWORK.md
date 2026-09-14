# P02 Basler fixed network

Applied 2026-09-13 to camera serial 24260480 (acA2500-14gm), MAC 00:30:53:41:02:80.

- Jetson camera interface: enP8p1s0
- NetworkManager profile: mono-basler-camera
- Jetson IPv4: 192.168.100.1/24
- Camera persistent IPv4: 192.168.100.2
- Camera subnet mask: 255.255.255.0
- Gateway: none (0.0.0.0); DHCP disabled on camera
- Wi-Fi remains the default route; Tailscale UI address remains 100.101.138.90.
- Existing Wired connection 1 profile preserved. New camera profile autoconnect priority: 10.
- monofactory identifies this camera by serial number; no application config change needed.

Verified persistent IP readback and rediscovery after RestartIpConfiguration.
Verified 15 consecutive live JPEG frames through monofactory BaslerStream, source 2592x1944 Mono8.
A physical power-cycle was not performed.

Check network:

    nmcli connection show mono-basler-camera
    ip -4 address show enP8p1s0

Start full UI:

    cd ~/Desktop/monofactory
    bash run_jetson.sh

For intentional restoration of the former host DHCP profile:

    sudo nmcli connection modify mono-basler-camera connection.autoconnect no
    sudo nmcli connection up uuid f5ff177c-a250-33fd-8ba9-581b6f45a6c0

Restoring host DHCP alone will not restore the camera's previous configuration.
The backup directory below contains the previous host profile details and camera IP settings.

Basler reference: https://docs.baslerweb.com/assigning-an-ip-address-to-a-camera

Backup: /home/jetson/Desktop/monofactory/work/camera-network-backup-20260913-235202
