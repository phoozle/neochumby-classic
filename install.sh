#!/bin/sh
# install.sh: install neochumby-classic on the server that runs neochumbyd.
#
# It installs this program only (neochumbyd must already be there): a Python venv with this
# package, a dedicated user in neochumbyd's group, and two systemd services, the renderer
# and the web page. Re-run it to upgrade or to change a setting: it is idempotent, and it
# remembers the settings it was given (in /etc/neochumby-classic/env).
#
#   sudo ./install.sh [options]
#   sudo ./install.sh --uninstall [--purge]
#
# Options (defaults in brackets; a re-run keeps the last values):
#   --user NAME          the services' user [neochumby-classic]; created if missing
#   --group NAME         neochumbyd's group [neochumbyd]; the user is added to it
#   --prefix DIR         where the venv goes [/opt/neochumby-classic]
#   --state DIR          config, photos, caches, renders [/var/lib/neochumby-classic]
#   --out DIR            the renders, one directory per Chumby [STATE/out]
#   --admin-socket PATH  neochumbyd's admin socket [/var/lib/neochumbyd/admin.sock]
#   --tree DIR           neochumbyd's app tree [/var/lib/neochumbyd/tree]
#   --listen ADDR:PORT   the web page [0.0.0.0:8080]
#   --heif               also install pillow-heif (iPhone HEIC uploads)
#   --token              ask for the owner token again (after `neochumbyd token new`)
#   --widget SPEC        also install a widget: git+https://HOST/PATH@<40-hex commit>;
#                        may be given more than once; remembered in widgets.txt
#   --no-start           install, but don't start or restart the services
#   --uninstall          stop and remove the services, the venv and /etc/neochumby-classic
#                        (keeps STATE: your config, photos and renders)
#   --purge              with --uninstall: also remove STATE and the user
set -eu

die() { echo "install.sh: $*" >&2; exit 1; }
say() { echo "==> $*"; }

SRC=$(cd "$(dirname "$0")" && pwd)
ETC=/etc/neochumby-classic
ENVFILE=$ETC/env
UNITDIR=/etc/systemd/system
UNITS="neochumby-classic-renderer.service neochumby-classic-web.service"

[ "$(id -u)" = 0 ] || die "run it as root (sudo ./install.sh)"

# Earlier choices are the defaults for this run.
if [ -f "$ENVFILE" ]; then
	# shellcheck disable=SC1090
	. "$ENVFILE"
fi
USER_NAME=${CLASSIC_USER:-neochumby-classic}
GROUP_NAME=${CLASSIC_GROUP:-neochumbyd}
PREFIX=${CLASSIC_PREFIX:-/opt/neochumby-classic}
STATE=${NEOCHUMBY_CLASSIC_STATE:-/var/lib/neochumby-classic}
OUT=${NEOCHUMBY_CLASSIC_OUT:-}
SOCKET=${NEOCHUMBY_CLASSIC_ADMIN:-/var/lib/neochumbyd/admin.sock}
TREE=${NEOCHUMBY_CLASSIC_TREE:-/var/lib/neochumbyd/tree}
LISTEN=${NEOCHUMBY_CLASSIC_WEB:-0.0.0.0:8080}
HEIF=${CLASSIC_HEIF:-0}
ASK_TOKEN=0
START=1
UNINSTALL=0
PURGE=0
WIDGETS=""

need() { if [ $# -lt 2 ] || [ -z "$2" ]; then die "$1 needs a value"; fi; }
while [ $# -gt 0 ]; do
	case $1 in
	--user) need "$@"; USER_NAME=$2; shift ;;
	--group) need "$@"; GROUP_NAME=$2; shift ;;
	--prefix) need "$@"; PREFIX=$2; shift ;;
	--state) need "$@"; STATE=$2; shift ;;
	--out) need "$@"; OUT=$2; shift ;;
	--admin-socket) need "$@"; SOCKET=$2; shift ;;
	--tree) need "$@"; TREE=$2; shift ;;
	--listen) need "$@"; LISTEN=$2; shift ;;
	--heif) HEIF=1 ;;
	--token) ASK_TOKEN=1 ;;
	--widget) need "$@"; WIDGETS="$WIDGETS $2"; shift ;;
	--no-start) START=0 ;;
	--uninstall) UNINSTALL=1 ;;
	--purge) PURGE=1 ;;
	-h | --help) sed -n '2,32p' "$0"; exit 0 ;;
	*) die "unknown option $1 (see --help)" ;;
	esac
	shift
done
OUT=${OUT:-$STATE/out}

for v in "$USER_NAME" "$GROUP_NAME"; do
	echo "$v" | grep -Eq '^[a-z_][a-z0-9_-]{0,31}$' || die "not a user or group name: $v"
done
for v in "$PREFIX" "$STATE" "$OUT" "$SOCKET" "$TREE"; do
	case $v in
	/*) ;;
	*) die "paths must be absolute: $v" ;;
	esac
	echo "$v" | grep -q '[[:space:]]' && die "paths can't contain spaces: $v"
done
echo "$LISTEN" | grep -Eq '^[][0-9A-Za-z.:-]+:[0-9]{1,5}$' || die "--listen is ADDRESS:PORT"

have_systemd() { command -v systemctl >/dev/null 2>&1 && [ -d /run/systemd/system ]; }

# ---------------------------------------------------------------- uninstall
if [ "$UNINSTALL" = 1 ]; then
	if have_systemd; then
		for u in $UNITS; do
			systemctl disable --now "$u" 2>/dev/null || true
			rm -f "$UNITDIR/$u"
		done
		systemctl daemon-reload
	fi
	rm -rf "$PREFIX" "$ETC"
	say "removed the services, $PREFIX and $ETC"
	if [ "$PURGE" = 1 ]; then
		rm -rf "$STATE"
		[ "$OUT" = "${OUT#"$STATE"/}" ] && rm -rf "$OUT"
		if id "$USER_NAME" >/dev/null 2>&1 && [ "$USER_NAME" != "$GROUP_NAME" ] && [ "$USER_NAME" = neochumby-classic ]; then
			userdel "$USER_NAME" 2>/dev/null || true
		fi
		say "removed $STATE and the user $USER_NAME"
	else
		say "kept $STATE (config, photos, renders); --uninstall --purge removes it"
	fi
	exit 0
fi

# ---------------------------------------------------------------- checks
have_systemd || die "this needs systemd (or see docker-compose.yml)"
command -v python3 >/dev/null 2>&1 || die "python3 is missing: install Python 3.11 or newer"
python3 -c 'import sys; sys.exit(sys.version_info < (3, 11))' || die "Python 3.11 or newer is needed (python3 is $(python3 -V 2>&1))"
python3 -c 'import venv, ensurepip' 2>/dev/null || die "python3's venv module is missing (Debian: apt install python3-venv)"
getent group "$GROUP_NAME" >/dev/null || die "no group $GROUP_NAME: install and start neochumbyd first, or pass --group"
[ -f "$SRC/pyproject.toml" ] || die "run install.sh from its checkout"
for w in $WIDGETS; do
	echo "$w" | grep -Eq '^git\+https://[A-Za-z0-9.-]+/[A-Za-z0-9._/-]+@[0-9a-f]{40}$' ||
		die "--widget takes git+https://HOST/PATH@COMMIT, pinned to a full 40-hex commit: $w"
done

# ---------------------------------------------------------------- the user and directories
if ! id "$USER_NAME" >/dev/null 2>&1; then
	say "creating the user $USER_NAME"
	useradd --system --user-group --no-create-home --home-dir "$STATE" --shell /usr/sbin/nologin "$USER_NAME"
fi
if ! id -nG "$USER_NAME" | tr ' ' '\n' | grep -qx "$GROUP_NAME"; then
	say "adding $USER_NAME to $GROUP_NAME"
	usermod -a -G "$GROUP_NAME" "$USER_NAME"
fi
# STATE: the renderer's and the page's. OUT, refresh/ and info/: shared with neochumbyd's
# group (setgid, so new files and directories keep the group). docs/interface.md.
install -d -o "$USER_NAME" -g "$GROUP_NAME" -m 2750 "$STATE" "$OUT"
install -d -o "$USER_NAME" -g "$GROUP_NAME" -m 2770 "$STATE/refresh" "$STATE/info"
install -d -o root -g root -m 0755 "$ETC" "$ETC/assets"

# ---------------------------------------------------------------- the venv
say "installing into $PREFIX/venv"
[ -x "$PREFIX/venv/bin/python" ] || python3 -m venv "$PREFIX/venv"
PIP="$PREFIX/venv/bin/pip"
"$PIP" install --quiet --upgrade pip
if [ "$HEIF" = 1 ]; then SPEC="${SRC}[heif]"; else SPEC="$SRC"; fi
"$PIP" install --quiet --upgrade "$SPEC"
touch "$ETC/widgets.txt"
for w in $WIDGETS; do
	grep -qxF "$w" "$ETC/widgets.txt" || echo "$w" >>"$ETC/widgets.txt"
done
while read -r w; do
	[ -n "$w" ] || continue
	say "installing widget $w"
	"$PIP" install --quiet "$w"
done <"$ETC/widgets.txt"

# ---------------------------------------------------------------- settings
cat >"$ENVFILE" <<EOF
# Written by install.sh; re-run it to change these (it reads them back as its defaults).
CLASSIC_USER=$USER_NAME
CLASSIC_GROUP=$GROUP_NAME
CLASSIC_PREFIX=$PREFIX
CLASSIC_HEIF=$HEIF
NEOCHUMBY_CLASSIC_STATE=$STATE
NEOCHUMBY_CLASSIC_OUT=$OUT
NEOCHUMBY_CLASSIC_ADMIN=$SOCKET
NEOCHUMBY_CLASSIC_TREE=$TREE
NEOCHUMBY_CLASSIC_WEB=$LISTEN
NEOCHUMBY_CLASSIC_ASSETS=$ETC/assets
EOF
# kept from an earlier file if you added them by hand
[ -z "${NEOCHUMBY_CLASSIC_CHUMBYS:-}" ] || echo "NEOCHUMBY_CLASSIC_CHUMBYS=\"$NEOCHUMBY_CLASSIC_CHUMBYS\"" >>"$ENVFILE"
[ -z "${NEOCHUMBY_CLASSIC_EVERY:-}" ] || echo "NEOCHUMBY_CLASSIC_EVERY=$NEOCHUMBY_CLASSIC_EVERY" >>"$ENVFILE"
chmod 0644 "$ENVFILE"

# ---------------------------------------------------------------- the owner token
# Asked for once, checked with neochumbyd, and kept root-only. systemd hands it to the web
# page alone (LoadCredential), which uses it only to notice when it no longer works:
# approving and forgetting always use the token of the person signed in.
as_user() { runuser -u "$USER_NAME" -- env NEOCHUMBY_CLASSIC_ADMIN="$SOCKET" "$@"; }
ADMIN="$PREFIX/venv/bin/neochumby-classic-admin"
if [ "$ASK_TOKEN" = 1 ] || [ ! -s "$ETC/owner-token" ]; then
	tries=0
	while :; do
		if [ -t 0 ]; then
			# shellcheck disable=SC2016
			printf 'Owner token (on this server, `neochumbyd token` prints it): '
			stty -echo
			read -r TOKEN || TOKEN=""
			stty echo
			echo
		else
			read -r TOKEN || TOKEN=""
		fi
		echo "$TOKEN" | grep -Eq '^[0-9a-f]{32}$' || { echo "That isn't a token (32 hex digits)." >&2; TOKEN=""; }
		if [ -n "$TOKEN" ]; then
			set +e
			echo "$TOKEN" | as_user "$ADMIN" check
			rc=$?
			set -e
			[ "$rc" = 0 ] && break
			if [ "$rc" = 2 ]; then
				echo "Couldn't ask neochumbyd to check it (above); keeping it anyway." >&2
				break
			fi
		fi
		tries=$((tries + 1))
		if [ "$tries" -ge 3 ] || [ ! -t 0 ]; then die "no usable owner token; re-run with --token"; fi
	done
	umask 077
	printf '%s\n' "$TOKEN" >"$ETC/owner-token.new"
	mv "$ETC/owner-token.new" "$ETC/owner-token"
	umask 022
	chmod 0600 "$ETC/owner-token"
	chown root:root "$ETC/owner-token"
fi

# ---------------------------------------------------------------- systemd
PRIMARY=$(id -gn "$USER_NAME")
unit() { # unit NAME DESCRIPTION EXEC [EXTRA]
	cat >"$UNITDIR/$1" <<EOF
# Written by neochumby-classic's install.sh.
[Unit]
Description=$2
After=network-online.target neochumbyd.service
Wants=network-online.target

[Service]
User=$USER_NAME
Group=$PRIMARY
SupplementaryGroups=$GROUP_NAME
EnvironmentFile=$ENVFILE
ExecStart=$3
Restart=on-failure
RestartSec=5
UMask=0027
NoNewPrivileges=yes
PrivateTmp=yes
ProtectSystem=strict
ProtectHome=yes
ReadWritePaths=$STATE $OUT
$4

[Install]
WantedBy=multi-user.target
EOF
}
unit neochumby-classic-renderer.service "neochumby classic: renderer" "$PREFIX/venv/bin/neochumby-classic-renderer" ""
unit neochumby-classic-web.service "neochumby classic: my channels web page" "$PREFIX/venv/bin/neochumby-classic-web" \
	"LoadCredential=owner-token:$ETC/owner-token"
systemctl daemon-reload
if [ "$START" = 1 ]; then
	for u in $UNITS; do
		systemctl enable --quiet "$u"
		systemctl restart "$u"
	done
	say "started $UNITS"
fi

# ---------------------------------------------------------------- can it reach neochumbyd?
if as_user "$ADMIN" paired >/dev/null 2>&1; then
	say "$USER_NAME can use neochumbyd's admin socket"
else
	cat >&2 <<EOF
install.sh: $USER_NAME can't use neochumbyd's admin socket ($SOCKET) yet.
  neochumbyd answers its own user and root only (its admin socket is 0600), so being in the
  $GROUP_NAME group isn't enough until neochumbyd lets its group in. Until then, either:
    - run these services as neochumbyd's own user: sudo ./install.sh --user <neochumbyd's user>
    - or name the Chumbys to render: add NEOCHUMBY_CLASSIC_CHUMBYS="name1 name2" to
      $ENVFILE (the web page's Chumbys page still needs the socket).
EOF
fi
say "done: the web page is on http://$(hostname):${LISTEN##*:}/"
