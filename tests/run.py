"""Run the app's tests: each test_*.py in a process of its own, with this Python (the app's .venv, or the packaged
app's own Python).

    python tests/run.py                  every test
    python tests/run.py plugins hooks    only the tests whose names have these words

Nothing here touches your projects: each test makes a scratch data folder of its own (RECAP_DATA), and the made-up
comic site and projects are drawn by the tests. gallery-dl, Suwayomi and real script writers aren't needed.
"""
import argparse
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("words", nargs="*", help="only tests whose names have these words")
    args = ap.parse_args()
    tests = sorted(HERE.glob("test_*.py"))
    if args.words:
        tests = [t for t in tests if any(w in t.stem for w in args.words)]
    failed = []
    for t in tests:
        start = time.time()
        r = subprocess.run([sys.executable, "-X", "utf8", str(t)], cwd=str(HERE), capture_output=True, text=True,
                           encoding="utf-8", errors="replace")
        lines = (r.stdout or "").strip().splitlines()
        ok = r.returncode == 0 and lines and lines[-1].startswith("ALL PASS")
        print(f"{'ok  ' if ok else 'FAIL'} {t.stem:24} {time.time() - start:6.1f} s  {lines[-1] if lines else ''}")
        if not ok:
            failed.append(t.stem)
            for line in [x for x in lines if "FAIL" in x] or (r.stderr or "").strip().splitlines()[-15:]:
                print("       " + line)
    print("\nALL PASS" if not failed else f"\n{len(failed)} FAILED: {', '.join(failed)}")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
