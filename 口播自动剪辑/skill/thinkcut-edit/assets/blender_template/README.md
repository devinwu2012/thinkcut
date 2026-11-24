# Blender 出片模板

`thinkcut_edit.py blender-new <片子目录> <名字>` 复制到 `<片子目录>/Blender_<名字>/`。做法、硬步骤、文件分工见 `skill/thinkcut-edit/references/动画做法.md` 第 6–8 节。

顺序：在片子目录 `分镜规格.json` 给本工程的镜头写 `讲什么`、`口播要点`、`画面`、`运镜` 和 `"工程": "Blender_<名字>"`（2026-11-13 起不再单独写 `镜头.json`；老工程的照读）→ 写 `布局.json` 并过 `python3 布局检查.py` → 改 `场景.py`、`标签.json` → `python3 出片.py --冒烟` → 关系类镜头先给用户看逻辑草图并 `anim-confirm --kind 草图` → `python3 出片.py --样张 秒,秒` → 用户确认后 `anim-confirm --kind 样张` → `python3 出片.py --shot A10`（默认 1080P 审片版）/ 导出前 `--4k`（定稿）。

细则（2026-11-13）：
- 整段渲染先出到 `出片/<镜头>.新出.mp4`，标签逐帧自检 0 帧才换成正式的 `出片/<镜头>.mp4` 并登记；有压字就不登记，原来的正式文件不动（登记表、总装还是用通过的那一版），新出的留成 `出片/<镜头>_待查.mp4`，看完可以删。
- 只重渲部分镜头（`--shot A10`）时，`事件.json` 只替换这次渲的镜头的事件，别的镜头的事件保留。
- `thinkcut_edit.py doctor` 会查这台机器有没有 Blender（找不到只提示，不算没就绪；Blender 不在 `/Applications` 时设环境变量 `BLENDER`）。
- `anim-confirm --kind 草图/样张` 必须用 `--files` 带至少一张存在的图片（jpg/png/webp）。
