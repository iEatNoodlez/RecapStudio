#!/usr/bin/env bash
# Give an AppDir its own glibc, so the AppImage starts on any x86-64 Linux whatever C library it has: an old glibc
# (CentOS 7, Debian 10, a NAS's), a new one, or none at all (Alpine's musl). build.sh --server runs it.
#
#   packaging/linux/own_glibc.sh <AppDir>
#
# What it does:
#   - glibc/ gets this machine's loader and C library, and every other library from outside the AppDir the app's
#     programs load (libstdc++, zlib...), found with ldd, and the ones glibc loads by itself (names, DNS, unwinding).
#     Run it on the machine the packages were installed on (GitHub Actions: Ubuntu 22.04, glibc 2.35).
#   - each ELF program in the AppDir (python3.12, imageio's ffmpeg) becomes <name>.real, with a small sh script in
#     its place that starts it through that loader, found from where the script is (an AppImage is mounted somewhere
#     new on each start). With --argv0, Python's sys.executable is the script, so the Pythons the app starts (its
#     drawing processes, the plugin host) go through it too.
#   - no LD_LIBRARY_PATH: the system's own programs (its ffmpeg, Claude Code) keep the system's C library.
# It stops if a library is missing, or if one is a desktop's (X11, OpenGL): a server has none.
set -euo pipefail

D="$(cd "${1:?Usage: own_glibc.sh <AppDir>}" && pwd)"
G="$D/glibc"
LOADER=ld-linux-x86-64.so.2
[ "$(uname -m)" = x86_64 ] || { echo "This is for x86_64 AppDirs, on an x86_64 machine."; exit 1; }
[ -d "$D/python" ] || { echo "$D isn't Recap Studio's AppDir (no python/)"; exit 1; }
W="$(mktemp -d)"
trap 'rm -rf "$W"' EXIT
rm -rf "$G"
mkdir -p "$G"

is_elf() { [ "$(head -c 4 "$1" | tr -d '\000' | tail -c 3)" = ELF ]; }
dynamic() { ! ldd "$1" 2>&1 | grep -qE 'not a dynamic executable|statically linked'; }

echo "== Its own glibc"
# every library the AppDir's ELF files load from outside it (the loader itself and the kernel's vdso aside)
find "$D" -path "$G" -prune -o -type f \( -name '*.so' -o -name '*.so.*' -o -perm -u+x \) -print0 |
  while IFS= read -r -d '' f; do if is_elf "$f"; then ldd "$f" 2>/dev/null || true; fi; done |
  awk -v d="$D/" '/not found/ { print "MISSING " $1; next }
                  $2 == "=>" && $3 ~ /^\// && index($3, d) != 1 { print $1, $3 }' |
  sort -u > "$W/libs.txt"
if grep -q '^MISSING' "$W/libs.txt"; then
  echo "Libraries the app needs that this machine doesn't have:"; grep '^MISSING' "$W/libs.txt"; exit 1
fi
if grep -qE '^lib(X|xcb|GL|EGL|gtk|gdk|wayland)' "$W/libs.txt"; then
  echo "The app needs desktop libraries a server doesn't have:"; grep -E '^lib(X|xcb|GL|EGL|gtk|gdk|wayland)' "$W/libs.txt"
  exit 1
fi
# the rest of glibc's own libraries (a program may load them later: ctypes, names, DNS), and what it loads by itself
for lib in libc.so.6 libm.so.6 libmvec.so.1 libpthread.so.0 libdl.so.2 librt.so.1 libutil.so.1 libanl.so.1 \
           libresolv.so.2 libnss_files.so.2 libnss_dns.so.2 libgcc_s.so.1; do
  grep -q "^$lib " "$W/libs.txt" && continue
  path="$(/sbin/ldconfig -p | awk -v l="$lib" '$1 == l && /x86-64/ { print $NF; exit }')"
  [ -n "$path" ] && echo "$lib $path" >> "$W/libs.txt"
done
while read -r name path; do cp -L "$path" "$G/$name"; done < "$W/libs.txt"
cp -L "/lib64/$LOADER" "$G/$LOADER"
chmod 755 "$G"/*
"$G/$LOADER" --version | head -n 1 > "$G/VERSION"
echo "$(cat "$G/VERSION"), and: $(cut -d' ' -f1 "$W/libs.txt" | tr '\n' ' ')"

echo "== Its programs through it"
find "$D" -path "$G" -prune -o -type f -perm -u+x ! -name '*.so' ! -name '*.so.*' ! -name '*.real' -print |
  while read -r f; do
    is_elf "$f" && dynamic "$f" || continue
    mv "$f" "$f.real"
    rel="$(realpath --relative-to="$(dirname "$f")" "$G")"
    cat > "$f" <<EOF
#!/bin/sh
# Recap Studio's ${f##*/}, started through the AppImage's own glibc ($rel), so it runs whatever C library this
# machine has (packaging/linux/own_glibc.sh writes this). The program itself is ${f##*/}.real.
here=\$(dirname "\$(readlink -f "\$0")")
exec "\$here/$rel/$LOADER" --library-path "\$here/$rel" --argv0 "\$0" "\$here/${f##*/}.real" "\$@"
EOF
    chmod 755 "$f"
    echo "${f#"$D"/}"
  done

# (a quick check that they start this way)
"$D/python/bin/python3" -I -c 'import sys, ssl, sqlite3, ctypes; print("Python", sys.version.split()[0], ssl.OPENSSL_VERSION)'
for ff in "$D"/python/lib/python3*/site-packages/imageio_ffmpeg/binaries/ffmpeg-linux-*; do
  case "$ff" in *.real) continue ;; esac
  [ -f "$ff" ] && "$ff" -hide_banner -version | head -n 1
done
echo "Done: $D runs on its own glibc"
