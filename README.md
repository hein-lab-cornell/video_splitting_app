# Behavior Clipper

Watch a video, mark each behavior's start and end with a hotkey, and export frame-accurate clips named `video-name_behavior-name_N.mp4`, plus CSVs listing each event's frames.

## Install (once)

```bash
cd ~/Desktop/video_annotating_app
python3 -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements.txt
```

`imageio-ffmpeg` ships its own ffmpeg binary, so you don't need a separate ffmpeg install. A system ffmpeg on your PATH is used if it's there.

## Run

```bash
source .venv/bin/activate
python run_app.py
```

## Workflow

1. **File → New project…**: pick an empty folder. The behavior editor opens.
2. **Define behaviors**: give each one a name and a one-character hotkey (letter, digit or symbol) and a color. Names become part of the clip filenames, so use only letters, digits, `-`, `_` and `.`.
3. **Add videos…** or **Add folder…**, then double-click a video to open it.
4. **Annotate**: press a behavior's hotkey once at onset and again at offset. Behaviors can overlap, and each hotkey toggles on its own. Open behaviors show "● REC" and a hatched bar on the timeline.
5. **Review** in the Events table. Double-click an event to jump to it. Select an event, step to the right frame, then use **Set start** or **Set end** to fix its boundaries. You can also **Delete** events.
6. **Export**: use **Export new** (this video), **Export selected**, or **Export all videos…** (every event in the project that hasn't been exported yet).

The CSVs save automatically after every change. Clips are only written when you export.

## Keyboard shortcuts

| Key | Action |
|---|---|
| Space | Play / pause |
| ← / → | Step 1 frame |
| Shift + ← / → | Step 10 frames |
| ↑ / ↓ | Speed up / slow down (0.1×, 0.25×, 0.5×, 1×, 2×, 4×, 8×) |
| Home / End | First / last frame |
| *behavior key* | Start, then end, that behavior |
| Esc | Cancel behaviors that are started but not ended |
| Ctrl/Cmd + Z | Remove the most recently added event |

Clicking or dragging on the timeline seeks. Hotkeys don't fire while you're typing in the frame box.

## Project folder

```
my_project/
├── project.json                        behaviors, hotkeys, colors, video list, export settings
├── annotations/
│   ├── <video>_annotations.csv         one per video (autosaved)
│   └── all_annotations.csv             every video combined (handy for R: read.csv())
└── clips/
    └── <video>_<behavior>_<N>.mp4
```

You can reopen `project.json` with the same behaviors for new videos. You can also copy it into a new folder to start another project with the same behavior set.

### CSV columns

`video, behavior, number, clip_name, start_frame, end_frame, n_frames, start_time_s, end_time_s, duration_s, fps, exported, video_path`

- Frames are **0-indexed** and `end_frame` is **inclusive**, so `n_frames = end_frame - start_frame + 1`.
- `start_time_s = start_frame / fps` and `end_time_s = (end_frame + 1) / fps`, which is the end of the last frame.
- `N` counts up separately for each video and behavior. Deleting an event also deletes its exported clip, so a later clip can't be mistaken for it.

## Clip export details

- Frames are decoded with the same OpenCV decoder used during annotation, then encoded with ffmpeg as H.264 (`yuv420p`, `.mp4`, source frame rate, no audio). This makes each clip contain exactly the frames listed in the CSV.
- **Edit → Export settings** sets quality: CRF 18 (the default) looks visually lossless, and CRF 0 is lossless.
- If you change an event's start or end after export, it's marked un-exported so you can re-export it.

## Notes / limitations

- Very high-resolution video at 8× plays as fast as your machine can decode, skipping displayed frames. Frame-by-frame stepping is always exact.
- Stepping backward is instant for recently viewed frames because they're cached. Longer backward jumps need a seek.
- Two videos with the same file name (in different folders) can't be in one project, because their clip names would collide.
