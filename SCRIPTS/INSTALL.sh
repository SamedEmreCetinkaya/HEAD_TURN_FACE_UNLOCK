#!/bin/sh
# Installs head-turn-face-unlock. Works from any directory:
#   sudo sh /path/to/HEAD_TURN_FACE_UNLOCK/SCRIPTS/INSTALL.sh [--polkit] [--no-pam] [--components DIRECTORY]
#   --polkit           also let polkit dialogs use the face when only the letter y is typed
#                      (Debian family with the plain common-auth stack only)
#   --no-pam           install everything but leave PAM untouched
#   --components DIRECTORY  use local copies of the models, the Face Landmarker bundle and the LiteRT
#                      wheel instead of downloading them (still checksummed)
# Running it again updates the installed files; enrollments and settings are kept.
set -eu
ROOT=$(dirname "$(dirname "$(readlink -f "$0")")")
LIBRARY=/usr/local/lib/head-turn-face-unlock
LIBEXEC=/usr/local/libexec/head-turn-face-unlock
SETTINGS=/etc/head-turn-face-unlock
POLKIT=""
PAM=yes
COMPONENTS=""

fail() { echo "INSTALL FAILED: $*" >&2; exit 1; }

while [ $# -gt 0 ]; do
    case "$1" in
        --polkit) POLKIT=--polkit ;;
        --no-pam) PAM=no ;;
        --components) [ $# -ge 2 ] || fail "--components needs a directory"; COMPONENTS=$2; shift ;;
        *) sed -n '2,10p' "$0"; exit 2 ;;
    esac
    shift
done
[ "$(id -u)" = 0 ] || fail "run as root: sudo sh $0"

echo "Checking requirements"
python3 -I -B -c 'import sys; sys.exit(not (3, 10) <= sys.version_info[:2] <= (3, 14))' \
    || fail "Python 3.10 to 3.14 is required (the versions LiteRT is published for)"
python3 -I -B -c '
import sys, cv2, numpy
major, minor = (int(part) for part in cv2.__version__.split(".")[:2])
sys.exit((major, minor) < (4, 8) or not hasattr(cv2, "FaceRecognizerSF"))
' 2>/dev/null || fail "OpenCV 4.8 or newer for Python 3 is required (python3-opencv on Debian, Ubuntu 24.10+ and Fedora, python-opencv on Arch)"
command -v systemd-creds > /dev/null || fail "systemd-creds is required (systemd 250 or newer)"
SYSTEMD_VERSION=$(systemctl --version | awk 'NR == 1 { print $2 + 0 }')
[ "$SYSTEMD_VERSION" -ge 250 ] || fail "systemd 250 or newer is required, found $SYSTEMD_VERSION"
command -v loginctl > /dev/null || fail "loginctl is required"
python3 -I -B -c "import sys; sys.path.insert(0, '$ROOT/SOURCE'); import head_turn_face_unlock.v4l2_capture" \
    || fail "the V4L2 structures do not match this platform (64-bit Linux is required)"
find /usr/lib /lib /usr/lib64 -name pam_exec.so -path '*security*' 2>/dev/null | grep -q . \
    || fail "pam_exec.so is missing"

echo "Installing files"
install -d -m 755 "$LIBRARY" "$LIBRARY/head_turn_face_unlock" "$LIBEXEC"
find "$LIBRARY/head_turn_face_unlock" -maxdepth 1 -type f -name '*.py' -delete
install -m 644 "$ROOT"/SOURCE/head_turn_face_unlock/*.py "$LIBRARY/head_turn_face_unlock/"
install -m 755 "$ROOT/SOURCE/ENTRY/service" "$ROOT/SOURCE/ENTRY/pam-helper" "$LIBEXEC/"
install -m 755 "$ROOT/SOURCE/ENTRY/head-turn-face-unlock" /usr/local/sbin/
install -m 755 "$ROOT/SOURCE/ENTRY/face-sudo" /usr/local/bin/
if [ -n "$COMPONENTS" ]; then
    python3 -I -B "$ROOT/SCRIPTS/FETCH_COMPONENTS.py" "$LIBRARY" "$COMPONENTS" || fail "the models or LiteRT could not be installed"
else
    python3 -I -B "$ROOT/SCRIPTS/FETCH_COMPONENTS.py" "$LIBRARY" || fail "the models or LiteRT could not be installed"
fi
# Version 1 used YuNet; the 478-point mesh replaced it.
rm -f "$LIBRARY/models/face_detection_yunet_2023mar.onnx"

install -d -m 755 "$SETTINGS"
install -d -m 700 "$SETTINGS/templates"
[ -e "$SETTINGS/HEAD_TURN_FACE_UNLOCK.conf" ] || install -m 644 "$ROOT/CONFIG/HEAD_TURN_FACE_UNLOCK.conf" "$SETTINGS/"
python3 -I -B -c "import sys; sys.path.insert(0, '$LIBRARY'); from head_turn_face_unlock.config import load; load()" \
    || fail "$SETTINGS/HEAD_TURN_FACE_UNLOCK.conf is invalid"

# The administration command can replace a face: the sudo password cache must not be enough.
SUDOERS_STAGING=$(mktemp)
printf '%s\n' "# head-turn-face-unlock: changing enrollments always asks for the password" \
    "Defaults!/usr/local/sbin/head-turn-face-unlock timestamp_timeout=0" > "$SUDOERS_STAGING"
if command -v visudo > /dev/null && [ -d /etc/sudoers.d ]; then
    visudo -cqf "$SUDOERS_STAGING" || { rm -f "$SUDOERS_STAGING"; fail "the sudoers rule did not validate"; }
    install -m 440 "$SUDOERS_STAGING" /etc/sudoers.d/head-turn-face-unlock
fi
rm -f "$SUDOERS_STAGING"

echo "Starting the socket"
install -m 644 "$ROOT/SYSTEMD/head-turn-face-unlock.socket" "$ROOT/SYSTEMD/head-turn-face-unlock.service" /etc/systemd/system/
systemctl daemon-reload
systemctl stop head-turn-face-unlock.service 2> /dev/null || true
systemctl enable --now head-turn-face-unlock.socket
/usr/local/sbin/head-turn-face-unlock status || fail "the service does not answer; PAM was not changed"

if [ "$PAM" = yes ]; then
    echo "Adding the face to PAM (the password keeps working whenever the face does not)"
    python3 -I -B "$ROOT/SCRIPTS/EDIT_PAM.py" add $POLKIT || echo "Some PAM files were skipped; see above." >&2
fi

if [ -n "$(find "$SETTINGS/templates" -maxdepth 1 -type f 2> /dev/null)" ] \
    && /usr/local/sbin/head-turn-face-unlock status 2> /dev/null | grep -q "^loaded        nobody$"; then
    echo "Enrollments made with version 1 cannot be read by version 2: enroll again." >&2
fi

cat <<EOF

Installed. Next:
  sudo head-turn-face-unlock enroll     enroll your face (about a minute)
  sudo head-turn-face-unlock test       check it exactly as a login would
  face-sudo COMMAND                     sudo with the face; plain sudo still asks for the password
Keep a root shell open the first time you test the lock screen.
Remove everything with: sudo sh $ROOT/SCRIPTS/UNINSTALL.sh
EOF
