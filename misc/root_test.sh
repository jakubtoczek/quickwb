#!/usr/bin/env bash
# Checks where QuickWB.bat decides to put its runtime, without downloading one:
# it copies the launcher up to the last "set" of the env block, points the two
# roots at a sandbox, and prints what came out.  Run from Git Bash.
set -e
cd "$(dirname "$0")/.."
T=$(mktemp -d); trap 'rm -rf "$T"' EXIT
W=$(cygpath -w "$T")
n=$(grep -n 'UV_NO_MODIFY_PATH' QuickWB.bat | cut -d: -f1)
{ printf '@echo off\r\nset "SHARED_ROOT=%s\shared"\r\nset "PRIVATE_ROOT=%s\private"\r\n' "$W" "$W"
  sed -n "1,${n}p" QuickWB.bat | sed -e '/set "SHARED_ROOT=/d' -e '/set "PRIVATE_ROOT=/d' -e '/^@echo off/d'
  printf 'echo ROOT=%%ROOT%%\r\necho ENV=%%UV_PROJECT_ENVIRONMENT%%\r\n'
} > "$T/t.bat"
: > "$T/enter"; echo 2 > "$T/two"
run() { cmd //c "$W\t.bat" < "$1" 2>&1 | tr -d '\r' | grep -oE '(ROOT|ENV)=[^ ]+' | tr '\n' ' '; }
ck() { [[ $2 == *"$3"* ]] || { echo "FAIL $1: $2"; exit 1; }; echo "ok  $1"; }

mkdir -p "$T/private"; touch "$T/private/uv.exe"

# an existing private install wins over a shared root, and asks nothing
mkdir -p "$T/shared"; touch "$T/shared/uv.exe"
ck private-wins "$(run "$T/enter")" "ROOT=$W\private "

# shared root only -> our own env folder inside it
rm -rf "$T/private"
ck shared "$(run "$T/enter")" 'shared\envs\quickwb '

# nothing installed yet: Enter takes the shared default, 2 takes private
rm -rf "$T/shared"
ck fresh-enter "$(run "$T/enter")" "ROOT=$W\shared "
ck fresh-2     "$(run "$T/two")"   "ROOT=$W\private "
echo "root detection OK"
