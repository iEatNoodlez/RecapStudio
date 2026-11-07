"""The plugin hooks (plugins.Plugin), with a test plugin using every one and a made-up project rendered for real:
- project_setting: kept per project, offered in New project, saved by Project settings;
- settings_filter: changes a project's settings (app-wide ones too), never saved into it;
- script_instructions and title_instructions: added to the writers' requests;
- language: a comic language read with the built-in reader, or with a reader of its own made ready first;
- scenes: the chapter's scenes as the plugin wants them (here: the other way round), in the video;
- overlay: a picture over every frame, in the video, and part of the chapter piece's fingerprint;
- the video_rendered event;
- a plugin whose setup fails partway: everything it added is taken back."""
import json
import textwrap

import cv2
import numpy as np

from helpers import App, check, data_folder, done, make_project


def main():  # (the video is drawn in several processes: they import this file, and mustn't run it)
    data = data_folder({"voice": "af_heart"})
    LOGO = data / "logo.png"
    green = np.zeros((60, 60, 4), np.uint8)
    green[:, :, 1] = green[:, :, 3] = 255  # pure green (blue, green, red, alpha), opaque
    cv2.imwrite(str(LOGO), green)
    plugin = data / "plugins" / "hooks-test"
    plugin.mkdir(parents=True)
    (plugin / "plugin.json").write_text('{"id": "hooks-test", "name": "Hooks test", "version": "1", "api": 1}', encoding="utf-8")
    (plugin / "__init__.py").write_text(textwrap.dedent(f'''
    from pathlib import Path
    LOGO = {str(LOGO)!r}
    calls = []


    def setup(plugin):
        key = plugin.project_setting("mode", "")
        on = lambda s: s.get(key) == "on"
        plugin.settings_filter(lambda s: {{"voice": "zz_test", "captions_listen": False}} if on(s) else None)
        plugin.script_instructions(lambda s: "HOOK-SCRIPT: narrate in Testish." if on(s) else "")
        plugin.title_instructions(lambda s: "HOOK-TITLE" if on(s) else "")
        plugin.language("xx", "Testish", names="the Testish way")
        plugin.language("yy", "Readerish", reader=lambda: {{"rec_model_path": "nowhere.onnx"}},
                        ready=lambda job: job.update(message="Readerish reader ready"))

        def scenes(project, chapter, items):
            calls.append((project["id"], chapter["id"], len(items)))
            return list(reversed(items)) if on(project["settings"]) else None
        plugin.scenes(scenes)
        plugin.overlay(lambda s: {{"image": LOGO, "where": "top-left", "width": 0.2, "margin": 0.0}} if on(s) else None)
        plugin.on("video_rendered", lambda project, parts, shorts: (plugin.data_dir / "rendered.txt").write_text(
            project["id"] + ":" + ",".join(Path(x).name for x in parts)))
    '''), encoding="utf-8")
    broken = data / "plugins" / "broken-hooks"
    broken.mkdir(parents=True)
    (broken / "plugin.json").write_text('{"id": "broken-hooks", "name": "Broken", "version": "1", "api": 1}', encoding="utf-8")
    (broken / "__init__.py").write_text(textwrap.dedent('''
    def setup(plugin):
        plugin.project_setting("left_over", "x")
        plugin.settings_filter(lambda s: {"voice": "should_not_happen"})
        plugin.script_instructions(lambda s: "SHOULD-NOT-HAPPEN")
        plugin.language("zz", "Brokenish")
        plugin.step("left", "Should not be there", lambda job: None)
        raise RuntimeError("on purpose, halfway")
    '''), encoding="utf-8")
    VIDEO = {"resolution": 360, "fps": 10, "video_style": "stills", "orientation": "landscape", "transition_seconds": 0.0,
             "layout": "pages", "shorts": 0, "captions": False, "background_music": False}
    on = make_project(data, "Hooks on", settings={**VIDEO, "hooks-test.mode": "on"})
    off = make_project(data, "Hooks off", settings=VIDEO)

    app = App(data)
    store, pipeline = app.store, app.pipeline
    from recap import ocr, script, translate, video, youtube

    check("the test plugin loads", app.plugins.loaded["hooks-test"].get("error") is None, app.plugins.loaded["hooks-test"])
    check("the broken one is listed with its error", "on purpose" in (app.plugins.loaded["broken-hooks"].get("error") or ""))

    # ---- a broken setup leaves nothing behind ----
    check("its setting taken back", "broken-hooks.left_over" not in store.DEFAULT_SETTINGS
          and "broken-hooks.left_over" not in store.NEW_PROJECT_KEYS)
    check("its filter taken back", store.load(off)["settings"]["voice"] == "af_heart")
    check("its instructions taken back", "SHOULD-NOT" not in script.system_prompt(store.load(off)["settings"], []))
    check("its language taken back", "zz" not in ocr.LANGUAGES and "zz" not in ocr.READERS)
    check("its step taken back", "broken-hooks.left" not in pipeline.STEP_LABELS and "broken-hooks.left" not in pipeline.EXTRA_STEPS)

    # ---- project settings and the filter ----
    key = "hooks-test.mode"
    check("a plugin's project setting: a default, offered in New project", store.DEFAULT_SETTINGS.get(key) == ""
          and key in store.NEW_PROJECT_KEYS and key in app.get("/api/projects/new-settings").json())
    s = store.load(on)["settings"]
    check("the filter changes that project's settings, app-wide ones too", s["voice"] == "zz_test" and s["captions_listen"] is False)
    check("and no other project's", store.load(off)["settings"]["voice"] == "af_heart"
          and "captions_listen" not in store.load(off)["settings"])
    store.update(on, lambda p: p.update(name="Hooks on"))
    saved = json.loads((store.project_dir(on) / "project.json").read_text(encoding="utf-8"))["settings"]
    check("what it changes is never saved", "captions_listen" not in saved and "voice" not in saved and saved.get(key) == "on", saved)
    r = app.put(f"/api/projects/{off}/settings", json={key: "on", "not-a-setting": 1})
    check("Project settings saves a plugin's setting (and still drops unknown ones)", r.status_code == 200
          and store.load(off)["settings"]["voice"] == "zz_test" and "not-a-setting" not in store.load(off)["settings"])
    app.put(f"/api/projects/{off}/settings", json={key: ""})
    r = app.post("/api/projects", json={"name": "Made on", "settings": {**app.get("/api/projects/new-settings").json(), key: "on"}})
    check("New project keeps it", r.status_code == 200 and r.json()["settings"][key] == "on" and r.json()["settings"]["voice"] == "zz_test")

    # ---- the writers' instructions ----
    prompt = script.system_prompt(store.load(on)["settings"], [])
    check("added to the narration writer's request, before the answer's format",
          "HOOK-SCRIPT" in prompt and prompt.index("HOOK-SCRIPT") < prompt.index(script.OUTPUT_LINES[:40]))
    check("only where the plugin says", "HOOK-SCRIPT" not in script.system_prompt(store.load(off)["settings"], []))
    check("added to the title writer's", script.extra_instructions(youtube.EXTRA, store.load(on)["settings"]) == ["HOOK-TITLE"])

    # ---- languages ----
    check("a language: in the list, read with the built-in reader", ocr.LANGUAGES.get("xx") == "Testish"
          and ocr.model_for({"language": "xx"}) == "built-in" and ocr.language({"language": "xx"}) == "xx")
    check("translated from it, its names its way", "Testish" in translate.instructions({"language": "xx"})
          and "the Testish way" in translate.instructions({"language": "xx"}))
    check("one with a reader of its own", ocr.model_for({"language": "yy"}) == "lang:yy"
          and ocr._model_args("lang:yy") == {"text_score": 0.0, "use_cls": False, "rec_model_path": "nowhere.onnx"})
    job = {"cancel": False, "message": ""}
    pipeline._reader_ready(job, "yy")
    check("its reader made ready before Read text", job["message"] == "Readerish reader ready")

    # ---- the scenes and the overlay, in a real video ----
    items = [{"type": "scene", "images": [store.project_dir(on) / "panels" / "c0001_00000.png"], "audio": None, "narration": "x"}]
    check("the overlay is part of a chapter piece's fingerprint", video.piece_signature(items, store.load(on)["settings"], [])
          != video.piece_signature(items, {**store.load(on)["settings"], key: ""}, []))
    r = app.post(f"/api/projects/{on}/run", json={"steps": ["video"]})
    job = app.wait_job(on, 300)
    check("the video renders", job.get("status") == "done", job)
    out = store.project_dir(on) / "output" / "Hooks on.mp4"  # (named after the project)
    cap = cv2.VideoCapture(str(out))
    cap.set(cv2.CAP_PROP_POS_FRAMES, 5)  # half a second in: the first scene
    ok, frame = cap.read()
    cap.release()
    H, W = (frame.shape[:2]) if ok else (0, 0)
    mod = __import__("sys").modules["recap_plugin_hooks_test"]
    check("the scenes hook saw the chapter", (on, "c0001", 2) in mod.calls, mod.calls)
    first = frame[int(H * 0.75), int(W * 0.75)].astype(int) if ok else None
    check("its order is in the video (the second panel first)", ok and np.abs(first - [120, 90, 160]).max() < 40, first)
    corner = frame[int(H * 0.05), int(W * 0.05)].astype(int) if ok else None
    check("the overlay is drawn over the frame, where it said", ok and corner[1] > 200 and corner[0] < 60 and corner[2] < 60, corner)
    rendered = data / "plugin-data" / "hooks-test" / "rendered.txt"
    check("video_rendered heard, with the parts", rendered.exists() and rendered.read_text() == f"{on}:Hooks on.mp4",
          rendered.read_text() if rendered.exists() else None)
    r = app.post(f"/api/projects/{off}/run", json={"steps": ["video"]})
    job = app.wait_job(off, 300)
    cap = cv2.VideoCapture(str(store.project_dir(off) / "output" / "Hooks off.mp4"))
    cap.set(cv2.CAP_PROP_POS_FRAMES, 5)
    ok, frame = cap.read()
    cap.release()
    check("a project without them: its own order, no overlay", ok and np.abs(frame[int(H * 0.75), int(W * 0.75)].astype(int)
                                                                         - [60, 0, 200]).max() < 40
          and frame[int(H * 0.05), int(W * 0.05)][1] < 200, frame[int(H * 0.05), int(W * 0.05)] if ok else None)
    done()


if __name__ == "__main__":
    main()
