# 口播自动剪辑

口播用 thinkcut-edit「剪口播」 剪完之后，这套能力按口播逐字卡点，接着做下面几件事：

- 做卡点动画（B 肉：Blender 写实三维或 HyperFrames 网页动画），长动画段加人脸小圆圈。
- 配音效，让每个音效最响的一下落在卡点上。
- 做白字黑底的新样式字幕和章节导航条。
- 在达芬奇里复制主线，搭好“卡点审核”时间线。
- 审完导出 4K 成片。

3:4、9:16、16:9 默认都出 4K。做法是在一条真实片子上摸索出来的，样例数据在 `samples/`。

## 怎么用

在 Claude 或 Codex 里说“自动剪辑这条片子”，并告诉它 thinkcut-edit「剪口播」 工程在哪。Skill 名是 `thinkcut-edit`，完整流程和规则见 [skill/thinkcut-edit/SKILL.md](skill/thinkcut-edit/SKILL.md)。

命令行（两边一样）：

```bash
python3 skill/thinkcut-edit/scripts/thinkcut_edit.py -h
```

做到一半想看进度、下一步跑什么：`python3 skill/thinkcut-edit/scripts/thinkcut_edit.py status <片子目录>`。

## 新 Mac

```bash
bash bin/setup_mac.sh
bash bin/run_tests.sh
```

`run_tests.sh` 是离线测试，约半分钟：不连达芬奇、不开浏览器，只用样例数据核对分镜、字幕（逐像素）、字幕整轨、改字和动画工程拼装。

## 目录

| 路径 | 内容 |
|---|---|
| `skill/thinkcut-edit/SKILL.md` | 流程与定稿规则 |
| `skill/thinkcut-edit/references/` | 分镜规格写法、动画做法、达芬奇实测坑与改后对齐 |
| `skill/thinkcut-edit/scripts/thinkcut_edit.py` | 命令入口，各步骤代码在 `scripts/thinkcut/` |
| `skill/thinkcut-edit/scripts/thinkcut/formats.py` | 三种 4K 画幅的版式数值（卡片、字幕、小圆圈、导航条） |
| `skill/thinkcut-edit/scripts/tools/` | 无头 Chrome 截图小工具 `cap.mjs`、`chrome.mjs`：重出老片子的代码动画时用（老代码动画模板 `assets/animation_template/` 2026-11-21 下线，新片子用 Blender、HyperFrames 两种模板；老做法见 `references/历史做法.md`） |
| `skill/thinkcut-edit/assets/sfx/` | 本机合成的刷墙、开裂、漏气声 |
| `samples/` | 一条片子的分镜规格、字时间、前 12 条字幕，离线测试用 |
| `bin/` | `setup_mac.sh` 接入新 Mac，`run_tests.sh` 离线测试 |
