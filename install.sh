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

# Make `memo` available in future interactive shells. Choose the startup file
# for the user's configured shell; the installer itself may be running under
# plain sh because it is commonly invoked through `curl | sh`.
SHELL_NAME=${SHELL:-}
SHELL_NAME=${SHELL_NAME##*/}
case "$SHELL_NAME" in
  zsh)  PROFILE="$HOME/.zshrc" ;;
  bash) PROFILE="$HOME/.bashrc" ;;
  fish)
    PROFILE="$HOME/.config/fish/config.fish"
    mkdir -p "$HOME/.config/fish"
    ;;
  *) PROFILE="$HOME/.profile" ;;
esac
case "$SHELL_NAME" in
  fish) PATH_LINE='fish_add_path -g "$HOME/.optmem"' ;;
  *)    PATH_LINE='export PATH="$HOME/.optmem:$PATH"' ;;
esac
if [ -f "$PROFILE" ] && grep -F "$PATH_LINE" "$PROFILE" >/dev/null 2>&1; then
  echo "The memo command is already configured on PATH in $PROFILE."
else
  printf '\n# OptMem command\n%s\n' "$PATH_LINE" >> "$PROFILE"
  echo "Added the memo command to PATH in $PROFILE."
fi
case ":${PATH:-}:" in
  *":$DIR:"*) ;;
  *) PATH="$DIR:${PATH:-}"; export PATH ;;
esac

case "$SHELL_NAME" in
  bash)
    COMPLETION_DIR="${XDG_DATA_HOME:-$HOME/.local/share}/bash-completion/completions"
    mkdir -p "$COMPLETION_DIR"
    "$DIR/memo" completion bash > "$COMPLETION_DIR/memo"
    echo "Installed Bash completion at $COMPLETION_DIR/memo."
    ;;
  zsh)
    COMPLETION_DIR="$HOME/.zfunc"
    mkdir -p "$COMPLETION_DIR"
    "$DIR/memo" completion zsh > "$COMPLETION_DIR/_memo"
    ZSH_COMPLETION_LINE='fpath=("$HOME/.zfunc" $fpath); autoload -Uz compinit && compinit'
    if ! grep -F "$ZSH_COMPLETION_LINE" "$PROFILE" >/dev/null 2>&1; then
      printf '\n# OptMem completion\n%s\n' "$ZSH_COMPLETION_LINE" >> "$PROFILE"
    fi
    echo "Installed Zsh completion at $COMPLETION_DIR/_memo."
    ;;
  fish)
    COMPLETION_DIR="${XDG_CONFIG_HOME:-$HOME/.config}/fish/completions"
    mkdir -p "$COMPLETION_DIR"
    "$DIR/memo" completion fish > "$COMPLETION_DIR/memo.fish"
    echo "Installed Fish completion at $COMPLETION_DIR/memo.fish."
    ;;
esac
echo "Open a new shell to use 'memo', or run: export PATH=\"\$HOME/.optmem:\$PATH\""
echo
"$DIR/memo" init
