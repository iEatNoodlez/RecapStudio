#!/usr/bin/env bash
# Build Recap Studio for Linux as an AppImage, in dist/: Lite (the app, with no plugins) or Full Suite (the app with
# the feature plugins on Recap Studio's own plugin list built in, as on Windows), for the machine it's built on,
# x86_64 or aarch64 (64-bit ARM: a Raspberry Pi 4 or 5, an ARM NAS or server):
#   Recap-Studio-Lite-<version>-<arch>.AppImage
#   Recap-Studio-Full-<version>-<arch>.AppImage
#
#   packaging/linux/build.sh 1.0.0 lite
#   packaging/linux/build.sh 1.0.0 full                         # its plugins from GitHub (packaging/plugins.json)
#   packaging/linux/build.sh 1.0.0 full --plugins-from ../Plugins   # or from a checkout of the list
#   packaging/linux/build.sh 1.0.0 full --server                # the server (RecapStudio-Cloud's), with no window:
#                                                                 Recap-Studio-Server-Full-<version>-<arch>.AppImage
#
# One file that runs on most distributions: the AppImage carries its own Python (python-build-standalone, as Windows'
# build does), the packages (with Qt for the window) and the app, and mounts itself when started. The desktop's needs
# a glibc as new as the machine it was built on: GitHub Actions builds it on Ubuntu 22.04 (.github/workflows/
# build.yml), so it runs on Ubuntu 22.04 and later, Debian 12 and later, Fedora 36 and later. The server's carries
# that glibc too (own_glibc.sh), so it runs on any Linux of its architecture: older ones, and Alpine's musl. The
# app's name for the architecture is uname -m's, which its updater matches (recap/updates.py, platform.machine()).
#
# Needs: bash, curl, tar, sha256sum, file (appimagetool's), and the few X libraries Qt needs that not every desktop
# has (they go inside it):
#   sudo apt install libxcb-cursor0 libxcb-icccm4 libxcb-keysyms1 libxcb-image0 libxcb-render-util0 libxkbcommon-x11-0
# Python, appimagetool and the AppImage runtime are downloaded at fixed versions, each checked against its SHA-256.
set -euo pipefail

VERSION="${1:-0.0.0-dev}"
EDITION="${2:-lite}"
shift $(( $# < 2 ? $# : 2 ))
PLUGINS_FROM=""
SERVER=""
while [ $# -gt 0 ]; do
  case "$1" in
    --plugins-from) PLUGINS_FROM="$(cd "$2" && pwd)"; shift 2 ;;
    --server) SERVER=1; shift ;;
    *) echo "Unknown option: $1"; exit 2 ;;
  esac
done
case "$EDITION" in
  lite) NAME=Lite ;;
  full) NAME=Full ;;
  *) echo "The edition is lite or full, not $EDITION"; exit 2 ;;
esac
if ! [[ "$VERSION" =~ ^[0-9A-Za-z.-]+$ ]]; then
  echo "The version can only have letters, digits, dots and dashes: $VERSION"; exit 2
fi
# a relocatable CPython (the same release as Windows'), and AppImage's own tools, for this machine's architecture
# (the SHA-256s are the ones GitHub lists for each file)
ARCH="$(uname -m)"
case "$ARCH" in
  x86_64)
    PYTHON_SHA256=6a79e922e50006b601f29e688407659da1c2b86ebcd4e9c7254cf234be8b89de
    TOOL_SHA256=ed4ce84f0d9caff66f50bcca6ff6f35aae54ce8135408b3fa33abfc3cb384eb0
    RUNTIME_SHA256=2fca8b443c92510f1483a883f60061ad09b46b978b2631c807cd873a47ec260d
    LDCONFIG_ARCH=x86-64 ;;  # (how ldconfig -p names a library's architecture)
  aarch64)
    PYTHON_SHA256=a945427411dc6851a1393533b74a4a1ac434321ae397f2ec69897a9b4fde032b
    TOOL_SHA256=f0837e7448a0c1e4e650a93bb3e85802546e60654ef287576f46c71c126a9158
    RUNTIME_SHA256=00cbdfcf917cc6c0ff6d3347d59e0ca1f7f45a6df1a428a0d6d8a78664d87444
    LDCONFIG_ARCH=AArch64 ;;
  *) echo "This builds the x86_64 or aarch64 AppImage, on a machine of that kind, not $ARCH."; exit 1 ;;
esac
PYTHON_URL="https://github.com/astral-sh/python-build-standalone/releases/download/20261026/cpython-3.12.16%2B20261026-$ARCH-unknown-linux-gnu-install_only.tar.gz"
TOOL_URL="https://github.com/AppImage/appimagetool/releases/download/1.9.1/appimagetool-$ARCH.AppImage"
# the static runtime: members need FUSE (every desktop has it), not libfuse2
RUNTIME_URL="https://github.com/AppImage/type2-runtime/releases/download/20251108/runtime-$ARCH"
# Qt's X11 window needs these; not every desktop has them, so they go next to Qt's own libraries (found there first)
QT_EXTRA_LIBS=(libxcb-cursor.so.0 libxcb-icccm.so.4 libxcb-keysyms.so.1 libxcb-image.so.0 libxcb-render-util.so.0
               libxcb-util.so.1 libxkbcommon-x11.so.0)

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$ROOT"
DIST="$ROOT/dist"
CACHE="$ROOT/build"
APPDIR="$DIST/AppDir-$EDITION${SERVER:+-server}"
OUT="$DIST/Recap-Studio-${SERVER:+Server-}$NAME-$VERSION-$ARCH.AppImage"
mkdir -p "$DIST" "$CACHE"
rm -rf "$APPDIR"
mkdir -p "$APPDIR"

fetch() {  # url sha256 file: downloaded once, and always checked
  local url="$1" sha="$2" file="$3"
  if [ ! -f "$file" ] || ! echo "$sha  $file" | sha256sum -c --status; then
    curl -fsSL --retry 3 -o "$file.part" "$url"
    mv -f "$file.part" "$file"
  fi
  if ! echo "$sha  $file" | sha256sum -c --status; then
    rm -f "$file"
    echo "$(basename "$file") doesn't match its SHA-256."; exit 1
  fi
}

echo "== Python"
fetch "$PYTHON_URL" "$PYTHON_SHA256" "$CACHE/python-3.12.16-linux-$ARCH.tar.gz"
tar -xzf "$CACHE/python-3.12.16-linux-$ARCH.tar.gz" -C "$APPDIR"  # makes $APPDIR/python
PY="$APPDIR/python/bin/python3"
SITE="$APPDIR/python/lib/python3.12/site-packages"

echo "== Packages"
# the CPU build of ONNX Runtime (requirements.txt), ffmpeg from imageio-ffmpeg (the app prefers the system's, which
# can use the graphics card), and Qt for the window
# (the server has no window: no Qt, about half the size)
"$PY" -m pip install --no-warn-script-location --disable-pip-version-check --no-compile -r requirements.txt \
  imageio-ffmpeg $([ -z "$SERVER" ] && echo "pywebview[qt]")
# one OpenCV, the headless one requirements.txt asks for: rapidocr asks for opencv-python as well, the same cv2 with
# Qt 5 inside, which needs X11 and OpenGL libraries a server or a NAS doesn't have (whichever installed last was cv2)
CV_VERSION="$("$PY" -m pip show opencv-python-headless | awk '/^Version:/ { print $2 }')"
"$PY" -m pip uninstall -y --disable-pip-version-check opencv-python > /dev/null
"$PY" -m pip install --no-warn-script-location --disable-pip-version-check --no-compile --force-reinstall --no-deps \
  "opencv-python-headless==$CV_VERSION"

[ -z "$SERVER" ] && echo "== Qt's extra libraries"
QT_LIB="$SITE/PyQt6/Qt6/lib"
for lib in $([ -z "$SERVER" ] && echo "${QT_EXTRA_LIBS[@]}"); do
  src="$(/sbin/ldconfig -p | awk -v l="$lib" -v a="$LDCONFIG_ARCH" '$1 == l && index($0, a) { print $NF; exit }')"
  if [ -z "$src" ]; then
    echo "$lib isn't installed here: sudo apt install libxcb-cursor0 libxcb-icccm4 libxcb-keysyms1 libxcb-image0" \
         "libxcb-render-util0 libxkbcommon-x11-0"; exit 1
  fi
  cp -L "$src" "$QT_LIB/$lib"
done

echo "== App"
cp -r recap "$APPDIR/recap"
mkdir -p "$APPDIR/.claude/skills"
cp -r .claude/skills/recap-studio-narration "$APPDIR/.claude/skills/"
cp README.md LICENSE "$APPDIR/"
find "$APPDIR/recap" "$APPDIR/.claude" -name __pycache__ -prune -exec rm -rf {} +
printf '%s\n' "$VERSION" > "$APPDIR/VERSION"
printf '%s\n' "$EDITION" > "$APPDIR/EDITION"
: > "$APPDIR/INSTALLED"  # (recap/paths.py: its own folder is read-only, so data goes in the XDG folders)
[ -n "$SERVER" ] && : > "$APPDIR/SERVER"  # (recap/__main__.py: it starts as a server; it updates from RecapStudio-Cloud)

if [ "$EDITION" = full ]; then
  echo "== Full Suite's plugins"
  bundle=(-I packaging/bundle_plugins.py "$APPDIR")
  [ -n "$PLUGINS_FROM" ] && bundle+=(--from "$PLUGINS_FROM")
  "$PY" "${bundle[@]}"
  # Python packages a plugin needs that the app doesn't have (none do yet)
  for req in "$APPDIR"/plugins/*/requirements.txt; do
    [ -f "$req" ] && "$PY" -m pip install --no-warn-script-location --disable-pip-version-check --no-compile -r "$req"
  done
fi

# everything compiled ahead: the AppImage is read-only, so Python couldn't keep what it compiles, and would compile
# it all again on every start (a few files that don't compile are only tests)
"$PY" -m compileall -q -j 0 "$APPDIR/python/lib/python3.12" "$APPDIR/recap" \
  $([ -d "$APPDIR/plugins" ] && echo "$APPDIR/plugins") > /dev/null || true

# the server: its own glibc, and its programs started through it (a desktop's Qt and graphics drivers need the
# system's own)
[ -n "$SERVER" ] && bash packaging/linux/own_glibc.sh "$APPDIR"

echo "== AppImage"
cp packaging/linux/AppRun "$APPDIR/AppRun"
chmod 755 "$APPDIR/AppRun"
cp packaging/linux/recap-studio.desktop "$APPDIR/recap-studio.desktop"
if [ -n "$SERVER" ]; then  # (run from a terminal or a service, not a menu)
  sed -i -e 's/^Name=.*/Name=Recap Studio Server/' -e 's/^Terminal=.*/Terminal=true/' "$APPDIR/recap-studio.desktop"
fi
cp packaging/launcher/icon.png "$APPDIR/recap-studio.png"
mkdir -p "$APPDIR/usr/share/icons/hicolor/256x256/apps"
cp packaging/launcher/icon.png "$APPDIR/usr/share/icons/hicolor/256x256/apps/recap-studio.png"
ln -sf recap-studio.png "$APPDIR/.DirIcon"

fetch "$TOOL_URL" "$TOOL_SHA256" "$CACHE/appimagetool-1.9.1-$ARCH.AppImage"
fetch "$RUNTIME_URL" "$RUNTIME_SHA256" "$CACHE/runtime-$ARCH-20251108"
chmod 755 "$CACHE/appimagetool-1.9.1-$ARCH.AppImage"
rm -f "$OUT"
# (appimagetool is an AppImage itself: unpacked to run, so the build needs no FUSE)
ARCH=$ARCH APPIMAGE_EXTRACT_AND_RUN=1 "$CACHE/appimagetool-1.9.1-$ARCH.AppImage" --no-appstream \
  --runtime-file "$CACHE/runtime-$ARCH-20251108" --comp zstd "$APPDIR" "$OUT"
rm -rf "$APPDIR"
echo "Done: $OUT ($(du -h "$OUT" | cut -f1))"
