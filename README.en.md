# ThinkCut · 思刻 (English summary)

> The full documentation is in Chinese: [README.md](README.md). This page is a short summary for English readers.

## What it is

ThinkCut is an AI-operated workflow for talking-head ("口播") videos on macOS, from script to finished video. The idea in one line: **AI does the work, a human makes the calls.**

- **No built-in AI.** You run your own AI coding assistant (Claude Code or Codex, signed in with your own account) in this folder. ThinkCut never touches your AI account or password.
- **The AI does the repetitive work:** transcription, picking retakes, cutting flubs, subtitles, animations, text overlays and sound effects, the chapter bar, building timelines in DaVinci Resolve, covers, titles/descriptions/tags, and publishing drafts.
- **You decide** on a local web page (the "review desk", opened only on your own Mac): look, approve, choose, comment directly on the text, and send changes back to the AI.
- **Editing and viewing happen in DaVinci Resolve Studio**; the review page is linked to it.
- **Two gates always need your explicit OK:** exporting the full video, and publishing it.

## Current limitations (read first)

This is an **early version, verified only on the author's own machines**, and it does not install in one step.

| Works out of the box | Not yet / bring your own |
|---|---|
| Linking the 5 skills to Claude Code / Codex | **Transcription, cutting into Resolve, automatic matting** need a "runtime environment" (Python 3.12, forced-alignment env and model, matting env, the Resolve connector) that the author installed by hand; from-scratch setup is not documented or tested elsewhere |
| Creating your own creator profile ("IP"), projects, home page, review desk (writing and revising the script with AI) | Fonts in `素材库/字体/` and the sound-effect library in `素材库/音效素材/` are not distributed |
| Cover layout, building the macOS app | Publishing only supports the Chinese tool “蚁小二” (Yixiaoer) |
| Offline tests | |

## Requirements (short)

macOS on Apple silicon (minimum version not yet confirmed), Xcode command line tools, Homebrew, ffmpeg, DaVinci Resolve Studio, Claude Code or Codex, the system Python 3.9 with the packages in `requirements/base.txt`. Optional: Doubao (ByteDance) speech recognition / TTS keys, Node 22+, Chrome, HyperFrames, Blender. See the Chinese README for the full table.

## Quick start

```bash
bash bin/setup_mac.sh --links            # link the skills for Claude Code / Codex
python3 bin/thinkcut_config.py new <name>  # create your own profile ("IP")
python3 -m pip install --user -r requirements/dev.txt
bash bin/fetch_fonts.sh                   # download the open-source fonts the tests need
python3 bin/test_all.py                   # offline tests (also run on GitHub Actions)
bash bin/build_app.sh --install           # optional: build ThinkCut.app (unsigned: right-click → Open the first time)
```

Then open Claude Code or Codex in this folder and say what you want ("start a project", "cut this video", "make a cover").

## Folder names

Folder and file names are currently Chinese (for example `core/` is the editing engine, `studio/` the review desk and edit skill, `projects/` project management, `cover/` covers, `chapters/` the chapter bar). Renaming the program folders to English is planned; project data folders you see in Finder (`01_原始素材`, `02_制作`, …) stay Chinese.

## License

Source-available under [PolyForm Noncommercial 1.0.0](LICENSE.md): free for noncommercial use; commercial use requires a separate license from the author. Bundled third-party code: see [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md). Contributing: [CONTRIBUTING.md](CONTRIBUTING.md). Security: [SECURITY.md](SECURITY.md). Changes: [RELEASE_NOTES.md](RELEASE_NOTES.md).
