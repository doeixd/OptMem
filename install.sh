#!/bin/sh
# OptMem installer. Run it again to update: it only replaces the tool, and
# `memo init` never touches memories that already exist.
#
#   curl -fsSL https://raw.githubusercontent.com/doeixd/OptMem/main/install.sh | sh
#
# Windows PowerShell uses install.ps1 instead.

set -eu
DIR="$HOME/.optmem"
URL="https://raw.githubusercontent.com/doeixd/OptMem/main/memo"
NEW="$DIR/memo.new"

command -v python3 >/dev/null || {
  echo "OptMem needs Python 3.7 or newer; python3 was not found." >&2
  exit 1
}
python3 -c 'import sys; raise SystemExit(sys.version_info < (3, 7))' || {
  echo "OptMem needs Python 3.7 or newer." >&2
  exit 1
}
command -v curl >/dev/null || {
  echo "The installer needs curl to download OptMem." >&2
  exit 1
}

mkdir -p "$DIR"
trap 'rm -f "$NEW"' EXIT HUP INT TERM
curl -fsSL "$URL" -o "$NEW"
python3 "$NEW" --help >/dev/null
chmod +x "$NEW"
mv "$NEW" "$DIR/memo"
chmod +x "$DIR/memo"
trap - EXIT HUP INT TERM

echo "Installed OptMem at $DIR/memo"
case ":${PATH:-}:" in
  *":$DIR:"*) echo "The memo command is available on PATH." ;;
  *) echo "PATH is optional; add $DIR if you want to type 'memo' directly." ;;
esac
echo
"$DIR/memo" init
