# Performance and the GPU

## GPU

With an NVIDIA graphics card and the GPU build of ONNX Runtime (with NVIDIA's CUDA and cuDNN libraries, about 1.3 GB), text reading and the voice run on the GPU. On an RTX 5060 Ti that measured about 0.28 s per panel for text reading (vs 0.81 s on the CPU) and 0.39 s per narration line for the voice (vs 2.11 s). Settings → Performance turns it off; if the GPU can't be used, both quietly run on the CPU, and the sidebar shows which. AMD and Intel graphics cards aren't used for text reading or the voice (they run on the CPU), but they do encode video (below).

Where the GPU build comes from:

- **From source**, `setup.ps1` installs it into `.venv` when it finds an NVIDIA card (`onnxruntime-gpu[cuda,cudnn]==1.31.0`).
- **The installer and the portable zip** carry the CPU build in their own Python, which an installed copy can't change (Program Files is read-only). **GPU support** is the GPU build as an optional download, offered when there's an NVIDIA card and the copy has only the CPU build: a **Download GPU support** button on first-run setup's CPU line, and a box under Settings → Performance (advanced mode). It's the same wheels `setup.ps1` installs, for the app's Python 3.12: ONNX Runtime GPU 1.31.0, NVIDIA's CUDA 13 libraries (runtime, cuBLAS, cuFFT, cuRAND, NVRTC, nvJitLink) and cuDNN 9.27, 1.33 GB from PyPI's file server, each wheel pinned in `recap\gpu.py` with its size and SHA-256 and checked as it comes in, with progress and Cancel (nothing half-downloaded is kept). They're unpacked into `addons\gpu` (`%LOCALAPPDATA%\Recap Studio\addons\gpu` for an installed copy), 1.9 GB on disk; about 3.2 GB must be free while they unpack. On the RTX 5060 Ti the download and unpacking took under a minute.
- GPU support is used from the next start: the app puts `addons\gpu` first on Python's import path before anything loads ONNX Runtime, so the GPU build is loaded instead of the CPU one, and it loads its CUDA and cuDNN libraries from beside it. Until then Settings, setup and the sidebar say it's downloaded and needs a restart. CUDA 13 needs NVIDIA driver 580 or newer; when the driver is older, Settings and setup say so before you download.
- **Remove GPU support** (Settings → Performance) puts text reading and the voice back on the CPU. Once loaded, its libraries can't be deleted while the app runs, so the app forgets it at once and deletes its files on the next start; removed before a restart, it's deleted straight away.

Tested on a copy of the portable build made to look installed, with its own `%APPDATA%` and `%LOCALAPPDATA%`: before the download both ran on the CPU (a voice sample in about 2.2 s); after it and a restart, both reported GPU (the same sample in 0.33 s); after Remove and a restart, the folder was gone and both were on the CPU again.

## Video export

Two settings under Settings → Performance control how fast video is made:

- **Video encoder**: *Automatic* uses the first GPU encoder that works with your ffmpeg build and graphics card: NVIDIA's (NVENC), AMD's (AMF) or Intel's (Quick Sync), each tried on a few frames first, and the CPU (libx264) otherwise; *CPU only* always uses the CPU. On an RTX 5060 Ti the NVIDIA encoder matched the source slightly more closely than the CPU one, with a ~13% smaller file. AMD's and Intel's run at about the same quality setting (constant quality, no bitrate target), but they haven't been tried on those cards: this was built on a PC with only an NVIDIA card, where it was checked that Automatic picks NVIDIA, and that an AMD or Intel encoder that won't start falls back to the CPU, for the video and the editors' clips. If an encoder fails to start, the export switches to the CPU on its own.
- **Draw video frames in this many processes**: frames are drawn by several processes at once, each taking turns of 6 frames, and handed to one ffmpeg in order, so the video is the same whichever number you pick (tested frame by frame). *Automatic* uses 2 (1 on computers with fewer than 4 cores for the NVIDIA encoder, or 6 for the CPU one). 1 draws everything in the app's own process. If the extra processes can't start, it draws in one. (Before 0.4.0 the processes could only keep 4 of their 30 frames ready, so in practice they took turns drawing instead of drawing at the same time; and a page polling a big project could slow the hand-off to ffmpeg to a tenth of its speed. Both are fixed: a 720p webtoon chapter that took 5 minutes renders in well under one.)

The job message shows what's being used, e.g. "Rendering video (GPU encoder, 2 drawing processes): 40%", and the Performance section says what Automatic means on your computer.

Measured on an RTX 5060 Ti with 12 CPU threads, exporting two scrolling webtoon chapters (12 minutes of video, 30 fps):

| Export | Frames per second | Speed vs. real time |
|---|---|---|
| 1080p, CPU encoder, before the export was sped up | 113 | 3.8× |
| 1080p, CPU encoder, 1 process | 157 | 5.2× |
| 1080p, CPU encoder, 2 processes | 169 | 5.6× |
| 1080p, NVIDIA encoder, 1 process | 227 | 7.6× |
| 1080p, NVIDIA encoder, 2 processes | 255 | 8.5× |
| 720p, NVIDIA encoder | 471 | 15.8× |
| 480p, NVIDIA encoder | 608 | 20.3× |
| 360p, NVIDIA encoder | 673 | 22.5× |
| 1080p stills (scenes one at a time), 30 fps | 330 | 11.0× |
| 1080p stills, 10 fps | (216) | 21.6× |

So an hour of 1080p video takes about 7 minutes with the NVIDIA encoder, and about 3 at 720p. More than 2 drawing processes, and faster NVENC presets (p1–p4), made no real difference at 1080p: by then the encoder itself is the limit. The NVIDIA encoder runs at preset p5, which gave the closest match to the source and the smallest file of the five presets.

On the GPU, several panels (or narration lines) are worked on at once, each worker with its own copy of the model. Text reading measured about 2x faster with 4 workers (0.14 s per panel) and no faster beyond; the voice gains about 1.2x with 2. Each worker needs roughly 1.5 GB of video memory, so **Automatic** (Settings → Performance) checks how much is free when a job starts and uses fewer workers when, say, Ollama's model is loaded. On the CPU it uses one worker, since extra workers there made reading slower. Results are identical to one-at-a-time, progress is still saved every 20 items in order, and Cancel stops within about a second.
