"""Pause during a render, with a made-up project rendered for real: Pause pressed (or the Overnight queue's stop
time come) while the last chapter is drawn holds the job before the join, with no video joined yet; Resume joins it,
named after the project. Pause before a chapter holds it there, as before."""
import time

from helpers import App, check, data_folder, done, make_project, until


def main():  # (the video is drawn in several processes: they import this file, and mustn't run it)
    data = data_folder({"voice": "af_heart"})
    VIDEO = {"resolution": 360, "fps": 10, "video_style": "stills", "orientation": "landscape", "transition_seconds": 0.0,
             "layout": "pages", "shorts": 0, "captions": False, "background_music": False}
    pid = make_project(data, "Paused", chapters=3, settings=VIDEO)
    app = App(data)
    store, pipeline = app.store, app.pipeline
    from recap import video

    drawn, pause_after = [], {2, 3}  # Pause pressed while the 2nd chapter is drawn, then while the 3rd (the last) is
    original = video.render_piece

    def render_piece(items, base, *args, **kw):
        original(items, base, *args, **kw)
        drawn.append(base.name)
        if len(drawn) in pause_after:
            app.post(f"/api/jobs/{pipeline.latest_for(pid)['id']}/pause")
    video.render_piece = render_piece

    out = store.project_dir(pid) / "output"
    joined = lambda: list(out.glob("*.mp4")) if out.is_dir() else []  # noqa: E731
    paused = lambda: (pipeline.latest_for(pid) or {}).get("status") == "paused"  # noqa: E731
    app.post(f"/api/projects/{pid}/run", json={"steps": ["video"]})

    check("Pause during a chapter: held before the next one", until(paused, 120) and len(drawn) == 2, len(drawn))
    time.sleep(1.5)
    check("and stays held there", paused() and len(drawn) == 2 and not joined(), drawn)
    app.post(f"/api/jobs/{pipeline.latest_for(pid)['id']}/resume")

    check("Pause during the last chapter: held before the join", until(lambda: paused() and len(drawn) == 3, 120),
          (pipeline.public(pipeline.latest_for(pid)), drawn))
    time.sleep(1.5)
    job = pipeline.latest_for(pid)
    check("nothing joined while it's held", paused() and not joined() and not store.load(pid).get("output"),
          ([x.name for x in joined()], job.get("message")))
    check("its message says it's paused", (job.get("message") or "").startswith("Paused"), job.get("message"))
    app.post(f"/api/jobs/{job['id']}/resume")
    until(lambda: not paused(), 10)  # (it looks every 0.3 s)
    job = app.wait_job(pid, 300)
    check("Resume joins it", job.get("status") == "done" and [x.name for x in joined()] == ["Paused.mp4"]
          and store.load(pid)["output"]["video"] == "output/Paused.mp4", (job, [x.name for x in joined()]))
    chs = [{"name": f"Chapter {k}"} for k in (1, 2, 3)]
    named = lambda name, some: pipeline._output_stem({"name": name, "chapters": chs}, some)  # noqa: E731
    check("the video is named after the project, some chapters with their range",
          (named("Solo Leveling", chs), named("Solo Leveling", chs[:2]), named("Solo Leveling", chs[1:2]))
          == ("Solo Leveling", "Solo Leveling - Chapter 1 to Chapter 2", "Solo Leveling - Chapter 2"))
    check("as a name Windows takes", (named("Re: Zero? <2>", chs), named("con", chs), named("  ", chs))
          == ("Re Zero 2", "con_", "Untitled"), (named("Re: Zero? <2>", chs), named("con", chs), named("  ", chs)))
    check("each chapter drawn once", len(drawn) == 3, drawn)
    done()


if __name__ == "__main__":
    main()
