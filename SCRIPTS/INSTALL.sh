#!/bin/sh
# Installs head-turn-face-unlock. Works from any directory:
#   sudo sh /path/to/HEAD_TURN_FACE_UNLOCK/SCRIPTS/INSTALL.sh [--polkit] [--no-pam] [--models DIRECTORY]
#   --polkit           also let polkit dialogs use the face when only the letter y is typed
#                      (Debian family with the plain common-auth stack only)
#   --no-pam           install everything but leave PAM untouched
#   --models DIRECTORY use local copies of the models instead of downloading (still checksummed)
# Running it again updates the installed files; enrollments and settings are kept.
set -eu
ROOT=$(dirname "$(dirname "$(readlink -f "$0")")")
LIBRARY=/usr/local/lib/head-turn-face-unlock
LIBEXEC=/usr/local/libexec/head-turn-face-unlock
SETTINGS=/etc/head-turn-face-unlock
POLKIT=""
PAM=yes
MODELS=""

fail() { echo "INSTALL FAILED: $*" >&2; exit 1; }

while [ $# -gt 0 ]; do
    case "$1" in
        --polkit) POLKIT=--polkit ;;
        --no-pam) PAM=no ;;
        --models) [ $# -ge 2 ] || fail "--models needs a directory"; MODELS=$2; shift ;;
        *) sed -n '2,8p' "$0"; exit 2 ;;
    esac
    shift
done
[ "$(id -u)" = 0 ] || fail "run as root: sudo sh $0"

echo "Checking requirements"
python3 -I -B -c 'import sys; sys.exit(sys.version_info < (3, 10))' || fail "Python 3.10 or newer is required"
python3 -I -B -c '
import sys, cv2, numpy
major, minor = (int(part) for part in cv2.__version__.split(".")[:2])
sys.exit((major, minor) < (4, 8) or not hasattr(cv2, "FaceDetectorYN") or not hasattr(cv2, "FaceRecognizerSF"))
' 2>/dev/null || fail "OpenCV 4.8 or newer for Python 3 is required (python3-opencv on Debian, Ubuntu 24.10+ and Fedora, python-opencv on Arch)"
command -v systemd-creds > /dev/null || fail "systemd-creds is required (systemd 250 or newer)"
SYSTEMD_VERSION=$(systemctl --version | awk 'NR == 1 { print $2 + 0 }')
[ "$SYSTEMD_VERSION" -ge 250 ] || fail "systemd 250 or newer is required, found $SYSTEMD_VERSION"
command -v loginctl > /dev/null || fail "loginctl is required"
python3 -I -B -c "import sys; sys.path.insert(0, '$ROOT/SOURCE'); import head_turn_face_unlock.v4l2_capture" \
    || fail "the V4L2 structures do not match this platform (64-bit Linux is required)"
for module in pam_exec.so; do
    find /usr/lib /lib /usr/lib64 -name "$module" -path '*security*' 2>/dev/null | grep -q . || fail "$module is missing"
done

echo "Installing files"
install -d -m 755 "$LIBRARY" "$LIBRARY/head_turn_face_unlock" "$LIBEXEC"
find "$LIBRARY/head_turn_face_unlock" -maxdepth 1 -type f -name '*.py' -delete
install -m 644 "$ROOT"/SOURCE/head_turn_face_unlock/*.py "$LIBRARY/head_turn_face_unlock/"
install -m 755 "$ROOT/SOURCE/ENTRY/service" "$ROOT/SOURCE/ENTRY/pam-helper" "$LIBEXEC/"
install -m 755 "$ROOT/SOURCE/ENTRY/head-turn-face-unlock" /usr/local/sbin/
install -m 755 "$ROOT/SOURCE/ENTRY/face-sudo" /usr/local/bin/
if [ -n "$MODELS" ]; then
    python3 -I -B "$ROOT/SCRIPTS/FETCH_MODELS.py" "$LIBRARY/models" "$MODELS" || fail "models could not be installed"
else
    python3 -I -B "$ROOT/SCRIPTS/FETCH_MODELS.py" "$LIBRARY/models" || fail "models could not be installed"
fi

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

cat <<EOF

Installed. Next:
  sudo head-turn-face-unlock enroll     enroll your face (about a minute)
  sudo head-turn-face-unlock test       check it exactly as a login would
  face-sudo COMMAND                     sudo with the face; plain sudo still asks for the password
Keep a root shell open the first time you test the lock screen.
Remove everything with: sudo sh $ROOT/SCRIPTS/UNINSTALL.sh
EOF
