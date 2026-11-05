# Linux

Recap Studio runs on Linux as well as Windows: the same app, in the same two editions (Lite and Full Suite), as an AppImage. That's one file that carries everything it needs, runs on most distributions and updates itself.

## Download and start

From the [Releases](https://github.com/iEatNoodlez/RecapStudio/releases) page:

| | AppImage |
|---|---|
| Lite | `Recap-Studio-Lite-<version>-x86_64.AppImage` |
| Full Suite | `Recap-Studio-Full-<version>-x86_64.AppImage` |

It needs a 64-bit PC (x86-64) and a distribution from 2022 or later: Ubuntu 22.04, Debian 12, Fedora 36, or newer (glibc 2.35 or later).

1. Keep the file wherever you like, for example in `~/Applications`.
2. Let it run: right-click it → **Properties** → **Allow executing file as program**, or `chmod +x Recap-Studio-*.AppImage`.
3. Double-click it, or run it from a terminal.

The first start walks you through setup, as on Windows. Its first page offers **Add it** to put Recap Studio in your applications menu, and you can do that later from **Settings → Run setup again**.

An AppImage mounts itself with FUSE, which desktop distributions have. If yours doesn't (a minimal install, or a container), start it with `--appimage-extract-and-run`.

## Where your files go

| | |
|---|---|
| Settings, API keys, logs, the voice model, add-ons | `~/.local/share/recap-studio` (or under `$XDG_DATA_HOME`) |
| Projects | `Recap Studio` in your Documents folder (Settings → Projects folder moves them) |
| The app | The AppImage, wherever you keep it |

**Updates:** the app checks for them when it starts, as on Windows. **Install** downloads the new AppImage beside the old one and checks it against the SHA-256 GitHub lists for it. It then puts it in the old one's place, under the same name, so menu entries and shortcuts keep working, and starts it. Your projects and settings aren't touched. The AppImage's folder has to be yours to change; if it isn't (`/opt`, say), download the new one from the Releases page instead.

**Uninstalling:** delete the AppImage and `~/.local/share/recap-studio`, and, if you added it to the menu, `~/.local/share/applications/recap-studio.desktop`. Your projects stay until you delete them.

## What's different from Windows

**Fonts.** Linux doesn't have the Windows fonts Recap Studio draws with, so each one has a stand-in. Microsoft's own fonts are used when they're installed (`ttf-mscorefonts-installer` on Ubuntu and Debian, `msttcore-fonts` on Fedora): then captions and thumbnails look as they do on Windows. Otherwise, setup's first page and the caption settings offer free look-alikes to download (4.7 MB from Google Fonts, under the SIL Open Font License). Until then, Recap Studio uses the closest font your distribution has:

| Windows' font | Used for | The look-alike | Without it |
|---|---|---|---|
| Arial Black | Captions | Archivo Black | Noto Sans Black, Ubuntu Sans ExtraBold, Cantarell Extra Bold, DejaVu Sans Bold… |
| Impact | Thumbnail titles, captions | Anton | Ubuntu Sans Condensed ExtraBold, Liberation Sans Narrow Bold, DejaVu Sans Condensed Bold… |
| Segoe UI Black | Thumbnail labels and bubbles, captions | Noto Sans Black | Ubuntu Sans ExtraBold, Cantarell Extra Bold… |
| Comic Sans (bold) | Re-lettered speech bubbles, captions | Comic Neue Bold | Arial Bold's stand-in |

**Video encoding.** Recap Studio uses your distribution's ffmpeg when it's installed, and its own copy otherwise. Its own copy can't use the graphics card, so install ffmpeg (`sudo apt install ffmpeg`; on Fedora, from RPM Fusion) for an NVIDIA card's encoder (NVENC). Intel and AMD encoders (VAAPI) aren't used on Linux yet, so with those cards videos are encoded on the CPU.

**GPU support** (NVIDIA): the same optional download as on Windows, for text reading and the voice. It's 1.6 GB, 2.5 GB once unpacked, and needs NVIDIA's driver 580 or later.

**Claude Code** is found where its installer and npm put it (`~/.local/bin`, `~/.npm-global/bin`, nvm's folders), even when the menu started the app without those on its path. **Log in to Claude Code** opens a terminal. If no terminal is found, the app shows the command to run.

**Your phone:** if a firewall runs on the computer (`ufw` or `firewalld`), it has to let devices on your network reach Recap Studio's port.

**The window** is Qt's web view, which is Chromium, like Windows' Edge window.

## When it opens in your browser instead

Qt's web view runs its pages in Chromium's sandbox, which needs user namespaces. Ubuntu 24.04 and later keep those from programs AppArmor doesn't know. Recap Studio checks before it opens its window. If the sandbox can't start, it opens in your default browser instead and says why. Everything works there except **Site check**, which needs a window of its own.

To get the window back on Ubuntu, give the AppImage an AppArmor profile that allows it user namespaces, as Ubuntu does for browsers. Save this as `/etc/apparmor.d/recap-studio`:

```
abi <abi/4.0>,
include <tunables/global>

profile recap-studio /tmp/.mount_Recap-*/** flags=(unconfined) {
  userns,
  include if exists <local/recap-studio>
}
```

Then load it, and start Recap Studio again:

```bash
sudo apparmor_parser -r /etc/apparmor.d/recap-studio
```

The profile covers what runs from inside a Recap Studio AppImage while it's mounted (`/tmp/.mount_Recap-…`).

## Claude Code's MCP server

To let Claude Code work with the open app (see [Script writers](script-writers.md#claude-code-the-mcp-server)), register the AppImage's MCP server once:

```bash
claude mcp add --scope user recap-studio -- ~/Applications/Recap-Studio-Lite-<version>-x86_64.AppImage --mcp
```

Use the path to your own AppImage. It updates in place, so the registration keeps working.

## Running from source

With Python 3.12 or newer, and its `venv` module (`sudo apt install python3.12-venv` on Ubuntu and Debian):

```bash
./setup.sh
./run.sh
```

`setup.sh` makes the Python environment, installs the packages with Qt for the window, adds GPU support when there's an NVIDIA card, and downloads the voice model. Qt's window needs the X libraries most desktops have. On a minimal install: `sudo apt install libxcb-cursor0 libxkbcommon-x11-0 libnss3`.

**Building the AppImages:** `packaging/linux/build.sh <version> lite` (or `full`) on Ubuntu 22.04, as GitHub Actions does for every release. The script's first lines say what it needs.
