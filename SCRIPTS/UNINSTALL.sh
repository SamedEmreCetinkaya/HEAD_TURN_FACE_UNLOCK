#!/bin/sh
# Removes head-turn-face-unlock. Works from any directory:
#   sudo sh /path/to/HEAD_TURN_FACE_UNLOCK/SCRIPTS/UNINSTALL.sh [--purge]
#   --purge  also delete the enrolled faces and the settings
# PAM is restored first, so logins never point at a removed helper.
set -eu
ROOT=$(dirname "$(dirname "$(readlink -f "$0")")")
PURGE=no
case "${1:-}" in
    --purge) PURGE=yes ;;
    "") ;;
    *) sed -n '2,5p' "$0"; exit 2 ;;
esac
[ "$(id -u)" = 0 ] || { echo "Run as root: sudo sh $0" >&2; exit 1; }

python3 -I -B "$ROOT/SCRIPTS/EDIT_PAM.py" remove
systemctl disable --now head-turn-face-unlock.socket 2> /dev/null || true
systemctl stop head-turn-face-unlock.service 2> /dev/null || true
rm -f /etc/systemd/system/head-turn-face-unlock.socket /etc/systemd/system/head-turn-face-unlock.service
systemctl daemon-reload
rm -f /etc/sudoers.d/head-turn-face-unlock
rm -f /usr/local/sbin/head-turn-face-unlock /usr/local/bin/face-sudo
rm -f /usr/local/libexec/head-turn-face-unlock/service /usr/local/libexec/head-turn-face-unlock/pam-helper
rmdir /usr/local/libexec/head-turn-face-unlock 2> /dev/null || true
find /usr/local/lib/head-turn-face-unlock/head_turn_face_unlock -maxdepth 1 -type f -name '*.py' -delete 2> /dev/null || true
rm -rf /usr/local/lib/head-turn-face-unlock/head_turn_face_unlock/__pycache__
rmdir /usr/local/lib/head-turn-face-unlock/head_turn_face_unlock 2> /dev/null || true
rm -f /usr/local/lib/head-turn-face-unlock/models/face_detection_yunet_2023mar.onnx \
      /usr/local/lib/head-turn-face-unlock/models/face_recognition_sface_2021dec.onnx \
      /usr/local/lib/head-turn-face-unlock/models/face_detector.tflite \
      /usr/local/lib/head-turn-face-unlock/models/face_landmarks_detector.tflite
rm -rf /usr/local/lib/head-turn-face-unlock/runtime /usr/local/lib/head-turn-face-unlock/runtime.new \
       /usr/local/lib/head-turn-face-unlock/runtime.old
rmdir /usr/local/lib/head-turn-face-unlock/models /usr/local/lib/head-turn-face-unlock 2> /dev/null || true
if [ "$PURGE" = yes ]; then
    find /etc/head-turn-face-unlock/templates -maxdepth 1 -type f -delete 2> /dev/null || true
    rmdir /etc/head-turn-face-unlock/templates 2> /dev/null || true
    rm -f /etc/head-turn-face-unlock/HEAD_TURN_FACE_UNLOCK.conf
    rmdir /etc/head-turn-face-unlock 2> /dev/null || true
    echo "Removed, including enrolled faces and settings."
else
    echo "Removed. Enrolled faces and settings remain in /etc/head-turn-face-unlock (use --purge to delete them)."
fi
