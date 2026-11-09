# Your phone

Recap Studio can be opened on a phone on the same Wi-Fi as the PC it runs on, to see how the recaps are going and to sort out one that stopped, without going to the PC.

## Linking a phone

1. On the PC: Settings → **Phone** → tick **Open Recap Studio on my phone (this Wi-Fi only)**. A QR code appears.
2. Scan it with the phone's camera and open the link. The phone keeps a small cookie with the code's key, so it only needs scanning once (until **Forget phones**).
3. If Windows asks whether to let Recap Studio on networks, allow it on **private** networks; otherwise the phone can't connect.

It works while Recap Studio is open on the PC, and it's remembered: the next time the app starts, the phone can open it again. Untick it to close it.

## What the phone shows

- **Every project**, as a row with its cover (the first page, or the video's thumbnail once there is one), what it's doing right now, why it stopped, or how far it is (chapters voiced, the video made), and **Make it** or **Continue** on one with something left to make. A recap that stopped to ask, or one running, sits above the list as a card of its own, with **Answer** or **Resume**, or **Pause** and **Cancel**.
- **A project**: its job, with **Pause**, **Resume** and **Cancel**; when it stopped, the reason (a script writer out of credit, say) and **Run again**, which carries on where it stopped once that's sorted; otherwise **Continue the recap** (or **Make the video again**). Under it: the thumbnail and title, **Watch the video**, each chapter's progress, and two settings: background music on or off, and the English in the pictures (for a translated comic). Those apply the next time the video is made.
- A recap that **stopped to ask about the characters** shows the people its scan found, with their pictures and the names the writer suggested: **Add** each with its name (or **Add all as suggested**), **The same as…** someone already named, or **Ignore** an extra; then **Resume**, and the script uses the names. The other stops (the wording check, the scan group's pages) still say to check them on the PC, or **Resume** to carry on without.
- **+ New** at the top makes a project: its name, the kind of comic and its language; the rest comes from your last project's choices, as on the PC.
- **Get chapters from a site…** in a project searches the sources the PC has (or opens one of their lists), picks the series, ticks the chapters it doesn't have yet and gets them; **Check for new chapters** does the same for a project's own series.

The page refreshes itself every few seconds while something runs.

## What's kept to the PC

- **Only the home network**: the phone page answers addresses on home networks (192.168.x.x, 10.x.x.x, 172.16-31.x.x, and their IPv6 equivalents), never the internet. Tailscale's addresses (100.x.x.x) aren't let in either.
- **Only linked phones**: anything else on the Wi-Fi without the key is turned away, with a note on how to link a phone. **Forget phones** makes a new key: phones that scanned the old one have to scan the new code.
- **Only the phone page**: from a phone, the app answers just what that page uses: each project's status, starting, pausing and cancelling its jobs, those two settings, its finished video and thumbnail, a new project, the character review's actions and pictures, and the sources' searches, lists and series. Not the app's settings or keys, and never a source or plugin installed from the phone: those are programs, and stay a choice made at the PC.
- The app's own window keeps using 127.0.0.1 as before; the phone gets a second listener (port 8790, or another free one), which only runs while Phone is ticked.
