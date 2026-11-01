# Your phone

Recap Studio can be opened on a phone on the same Wi-Fi as the PC it runs on, to see how the recaps are going and to sort out one that stopped, without going to the PC.

## Linking a phone

1. On the PC: Settings → **Phone** → tick **Open Recap Studio on my phone (this Wi-Fi only)**. A QR code appears.
2. Scan it with the phone's camera and open the link. The phone keeps a small cookie with the code's key, so it only needs scanning once (until **Forget phones**).
3. If Windows asks whether to let Recap Studio on networks, allow it on **private** networks; otherwise the phone can't connect.

It works while Recap Studio is open on the PC, and it's remembered: the next time the app starts, the phone can open it again. Untick it to close it.

## What the phone shows

- **Every project**, with what it's doing right now (the step and its progress), why it stopped, or how far it is (chapters voiced, the video made).
- **A project**: its job, with **Pause**, **Resume** and **Cancel**; when it stopped, the reason (a script writer out of credit, say) and **Run again**, which carries on where it stopped once that's sorted; otherwise **Continue the recap** (or **Make the video again**). Under it: the thumbnail and title, **Watch the video**, each chapter's progress, and two settings: background music on or off, and the English in the pictures (for a translated comic). Those apply the next time the video is made.
- A job waiting for you (the characters to name, the wording to check) says so; **Resume** carries on without, or check it on the PC.

The page refreshes itself every few seconds while something runs.

## What's kept to the PC

- **Only the home network**: the phone page answers addresses on home networks (192.168.x.x, 10.x.x.x, 172.16-31.x.x, and their IPv6 equivalents), never the internet. Tailscale's addresses (100.x.x.x) aren't let in either.
- **Only linked phones**: anything else on the Wi-Fi without the key is turned away, with a note on how to link a phone. **Forget phones** makes a new key: phones that scanned the old one have to scan the new code.
- **Only the phone page**: from a phone, the app answers just what that page uses (each project's status, starting, pausing and cancelling its jobs, those two settings, its finished video and thumbnail). Settings, API keys, the PC's folders, the full app and the QR code itself stay on the PC.
- The app's own window keeps using 127.0.0.1 as before; the phone gets a second listener (port 8790, or another free one), which only runs while Phone is ticked.
