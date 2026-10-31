"""Files the app downloads on request (models are never shipped with it), checked against their
size and SHA-256, with progress and Cancel. Nothing half-downloaded is kept."""
import hashlib
import json
import shutil
import threading
import time
import urllib.request
import zipfile
from pathlib import Path

HF_URL = "https://huggingface.co/{repo}/resolve/{rev}/{path}"


class Cancelled(Exception):
    pass


class Download:
    def __init__(self, folder: Path, files: dict, url: str = HF_URL, unzip: bool = False):
        """files: local name -> (repository, pinned revision, path in the repository, bytes, sha256).
        unzip: the files are zip archives, unpacked into the folder once checked (and then deleted)."""
        self.folder, self.files, self.url, self.unzip = Path(folder), files, url, unzip
        self.total = sum(f[3] for f in files.values())
        self.state = {"state": "idle", "progress": 0.0, "message": "", "error": None}
        self._cancel = threading.Event()
        self._lock = threading.Lock()

    @property
    def marker(self) -> Path:
        return self.folder / "installed.json"

    def installed(self) -> bool:
        return self.marker.exists() and (self.unzip or all((self.folder / n).exists() for n in self.files))

    def path(self, name: str) -> Path:
        return self.folder / name

    def status(self) -> dict:
        return {**self.state, "installed": self.installed(), "download_mb": round(self.total / 1e6, 1)}

    def start(self) -> dict:
        with self._lock:
            if self.state["state"] == "installing":
                raise ValueError("Already downloading")
            if self.installed():
                raise ValueError("Already installed")
            self._cancel.clear()
            self.state.update(state="installing", progress=0.0, message="Starting", error=None)
        threading.Thread(target=self._run, daemon=True, name=f"download-{self.folder.name}").start()
        return self.status()

    def cancel(self):
        if self.state["state"] == "installing":
            self._cancel.set()

    def remove(self) -> dict:
        if self.state["state"] == "installing":
            raise ValueError("Cancel the download first")
        shutil.rmtree(self.folder, ignore_errors=True)
        self.state.update(state="idle", progress=0.0, message="Removed", error=None)
        return self.status()

    def _run(self):
        tmp = self.folder / "partial"
        done = 0
        try:
            shutil.rmtree(tmp, ignore_errors=True)
            tmp.mkdir(parents=True)
            for name, (repo, rev, path, size, sha) in self.files.items():
                digest, got = hashlib.sha256(), 0
                req = urllib.request.Request(self.url.format(repo=repo, rev=rev, path=path),
                                             headers={"User-Agent": "RecapStudio"})
                with urllib.request.urlopen(req, timeout=60) as r, open(tmp / name, "wb") as f:
                    while chunk := r.read(1 << 20):
                        if self._cancel.is_set():
                            raise Cancelled()
                        f.write(chunk)
                        digest.update(chunk)
                        got += len(chunk)
                        self.state.update(progress=(done + got) / self.total,
                                          message=f"{(done + got) / 1e6:.1f} of {self.total / 1e6:.1f} MB")
                if got != size or digest.hexdigest() != sha:
                    raise RuntimeError(f"{path} from {repo} didn't download correctly (its size or checksum is wrong)")
                done += size
            for name in self.files:
                if self.unzip:  # unpacked in place, refusing anything that would land outside the folder
                    self.state.update(message=f"Unpacking {name}")
                    with zipfile.ZipFile(tmp / name) as z:
                        root = self.folder.resolve()
                        for member in z.namelist():
                            if not (root / member).resolve().is_relative_to(root):
                                raise RuntimeError(f"{name} holds a file outside its folder: {member}")
                        z.extractall(self.folder)
                    (tmp / name).unlink()
                else:
                    (tmp / name).replace(self.folder / name)
            tmp.rmdir()
            self.marker.write_text(json.dumps({"installed_at": time.time(), "files": list(self.files)}), encoding="utf-8")
            self.state.update(state="idle", progress=1.0, message="Installed")
        except Cancelled:
            shutil.rmtree(tmp, ignore_errors=True)
            self.state.update(state="idle", progress=0.0, message="Download cancelled; nothing was kept")
        except Exception as e:  # reported in the app; nothing half-downloaded is kept
            shutil.rmtree(tmp, ignore_errors=True)
            self.state.update(state="error", progress=0.0, message="The download failed", error=str(e))
