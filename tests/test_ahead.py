"""Make the recap works ahead while an online writer writes the script (pipeline._Ahead): each chapter the writer
finishes is voiced, then its video rendered, beside the script, so Voice and Render video only have what's left;
every line is voiced once and every chapter drawn once, rendered for real. A Cancel during the script stops the work
ahead too. With the local writer (Ollama), nothing is done ahead."""
import json
import re
import time

from helpers import App, check, data_folder, done, make_project

VIDEO = {"resolution": 360, "fps": 10, "video_style": "stills", "orientation": "landscape", "transition_seconds": 0.0,
         "layout": "pages", "shorts": 0, "captions": False, "background_music": False, "suggest_title": False}
WRITE = 4.0  # seconds the made-up online writer takes for a chapter


def unwritten(data, pid):
    """The script not written yet: no narration, no voice."""
    pj = data / "projects" / pid / "project.json"
    p = json.loads(pj.read_text(encoding="utf-8"))
    for x in p["panels"]:
        x.update(narration="", script_done=False, audio=None, duration=0.0)
    pj.write_text(json.dumps(p), encoding="utf-8")


def main():  # (the video is drawn in several processes: they import this file, and mustn't run it)
    import numpy as np
    data = data_folder({"voice": "af_heart", "llm_provider": "claude"})  # (the writer is an app-wide setting)
    pid = make_project(data, "Ahead", chapters=4, settings=VIDEO)
    local = make_project(data, "Local", chapters=2, settings=VIDEO)
    stopped = make_project(data, "Stopped", chapters=4, settings=VIDEO)
    for x in (pid, local, stopped):
        unwritten(data, x)
    app = App(data)
    store, pipeline = app.store, app.pipeline
    from recap import scenes, tts, video

    events = []  # (time, what, project, chapter)

    def speak(text, settings):  # (the narration starts with its project's id: see the writers below)
        m = re.match(r"(\w+) .*chapter (\d+)", text)
        events.append((time.time(), "voice", m.group(1), f"c{int(m.group(2)):04d}"))
        return np.zeros(int(tts.SAMPLE_RATE * 0.6), np.float32)
    tts.speak = speak
    original = video.render_piece

    def render_piece(items, base, *args, **kw):
        info = original(items, base, *args, **kw)
        folder = base.parent.parent
        owner = next((x for x in (pid, local, stopped) if store.project_dir(x) == folder), folder.name)
        events.append((time.time(), "render", owner, base.name.split("-")[0]))
        return info
    video.render_piece = render_piece

    def writer(job, p):  # an online writer: a chapter at a time, through the app's own _api_chapter
        for chapter in pipeline._chapters(job, p):
            pipeline._check(job)
            p = store.load(job["project"])
            groups = [g for g in scenes.groups(p["panels"]) if g[0]["chapter"] == chapter["id"]]

            def narrate(where, flat, context, fixed, scroll, n=int(chapter["id"][1:])):
                end = time.time() + WRITE
                while time.time() < end:
                    pipeline._check(job)
                    time.sleep(0.2)
                return ([(k, k, f"{job['project']} Panel {k - 1} of chapter {n} happens.") for k in range(1, len(flat) + 1)],
                        f"Part {n}")
            pipeline._api_chapter(job, p, chapter, groups, None, "a made-up writer", narrate)
            events.append((time.time(), "written", job["project"], chapter["id"]))
    pipeline._script_claude = writer

    def local_writer(job, p, targets, label=None):  # the local model, scene by scene
        for g in targets:
            time.sleep(0.5)
            n = int(g[0]["chapter"][1:])
            pipeline._merge(job["project"], "narration",
                            {g[0]["id"]: f"{job['project']} Panel {g[0]['id'][-1]} of chapter {n} happens."},
                            clear_audio=True, mark="script_done")
        events.append((time.time(), "written", job["project"], "all"))
    pipeline._script_ollama = local_writer

    # ---- an online writer: voiced and rendered as it goes --------------------------------------
    started = time.time()
    app.post(f"/api/projects/{pid}/run", json={"steps": ["script", "audio", "video"]})
    seen = ""
    while (pipeline.latest_for(pid) or {}).get("status") in ("queued", "running"):
        seen = seen or (pipeline.latest_for(pid) or {}).get("ahead") or ""
        time.sleep(0.1)
    job = pipeline.public(pipeline.latest_for(pid))
    check("Make the recap finishes", job.get("status") == "done", job)
    mine = [e for e in events if e[2] == pid]
    written = {e[3]: e[0] for e in mine if e[1] == "written"}
    renders = [e for e in mine if e[1] == "render"]
    voices = [e for e in mine if e[1] == "voice"]
    last_written = max(written.values())
    check("the writer wrote the 4 chapters", len(written) == 4, sorted(written))
    first_render = min((e[0] for e in renders if e[3] == "c0001"), default=None)
    check("Chapter 1 was voiced while the writer was still writing",
          any(e[3] == "c0001" and e[0] < last_written for e in voices),
          [(round(e[0] - started, 1), e[3]) for e in voices])
    check("and rendered while it was still writing", first_render is not None and first_render < last_written,
          (first_render and round(first_render - started, 1), round(last_written - started, 1)))
    check("chapters 1 to 3 were rendered before the script was done",
          sum(1 for e in renders if e[0] < last_written) >= 2,
          [(round(e[0] - started, 1), e[3]) for e in renders])
    check("every line voiced once", sorted(e[3] for e in voices) == sorted(["c0001", "c0002", "c0003", "c0004"] * 2),
          [e[3] for e in voices])
    check("every chapter drawn once (Render video reused the ones rendered ahead)",
          sorted(e[3] for e in renders) == ["c0001", "c0002", "c0003", "c0004"], [e[3] for e in renders])
    p = store.load(pid)
    d = store.project_dir(pid)
    check("every line has its voice", all(pipeline.audio_done(x, d) for x in p["panels"]),
          [x["id"] for x in p["panels"] if not pipeline.audio_done(x, d)])
    check("the video is made", (p.get("output") or {}).get("video") == "output/Ahead.mp4"
          and (d / "output" / "Ahead.mp4").stat().st_size > 1000, p.get("output"))
    check("the page was told what's done ahead", seen.startswith("Working ahead"), seen)
    check("and the job's note says so", "while the script was being written" in (job.get("message") or ""),
          job.get("message"))
    check("nothing left working ahead", not pipeline._AHEAD and not job.get("ahead"), (pipeline._AHEAD, job.get("ahead")))

    # ---- the local writer: nothing ahead ----------------------------------------------------------
    store.update_global({"llm_provider": "ollama"})
    app.post(f"/api/projects/{local}/run", json={"steps": ["script", "audio"]})
    job = app.wait_job(local, 120)
    mine = [e for e in events if e[2] == local]
    end = next(e[0] for e in mine if e[1] == "written")
    check("with Ollama writing, nothing is voiced before the script is done",
          job.get("status") == "done" and mine and all(e[0] >= end for e in mine if e[1] == "voice")
          and sum(1 for e in mine if e[1] == "voice") == 4, (job.get("status"), [e[1] for e in mine]))

    # ---- Cancel during the script: the work ahead stops too ---------------------------------------
    store.update_global({"llm_provider": "claude"})
    app.post(f"/api/projects/{stopped}/run", json={"steps": ["script", "audio", "video"]})
    t0 = time.time()
    while not any(e[2] == stopped and e[1] == "written" for e in events) and time.time() - t0 < 60:
        time.sleep(0.1)
    app.post(f"/api/jobs/{pipeline.latest_for(stopped)['id']}/cancel")
    job = app.wait_job(stopped, 120)
    at = time.time()
    time.sleep(3)
    late = [e for e in events if e[2] == stopped and e[0] > at]
    check("Cancel stops the script and the work ahead", job.get("status") == "cancelled" and not late
          and stopped not in [j["project"] for j in pipeline.jobs.values() if j["id"] in pipeline._AHEAD],
          (job.get("status"), late))
    done()


if __name__ == "__main__":
    main()
