# 口播剪辑（剪口播、配 B-roll、花字音效）

把一条中文单人口播原片，自动做成：选好重录的连贯初剪 + 字幕 + 达芬奇时间线。本目录集中存放程序、Skill 和豆包 Key；每条视频的文件放在各自的项目目录：标准项目放 `<项目根>/02_制作/` 下（剪口播 `<片名>_AI剪辑/`、B-roll `B-roll_AI/`、花字 `花字音效/`）；不是标准项目时默认原片旁的 `<片名>_AI剪辑/`；已有旧目录继续用。

- AI 操作步骤：[剪辑总入口](../口播自动剪辑/skill/thinkcut-edit/SKILL.md)、[口播剪辑](skills/a-roll/SKILL.md)、[配 B-roll](skills/b-roll/SKILL.md)；剪辑判断标准：[口播剪辑工作规范](skills/a-roll/references/editing-workflow.md)
- AI 必守规则与修改存档流程：[AGENTS.md](AGENTS.md)
- 更新记录：根目录 [CHANGELOG.md](../CHANGELOG.md)（2026-11-24 起全家一份；本文件夹的 [CHANGELOG.md](CHANGELOG.md) 是之前的记录）；当前版本：根目录 `VERSION`

## 能做什么

花字音效使用 [thinkcut-edit「花字音效」 Skill](skills/fx/SKILL.md)：读标记和画面后制定计划，用户同意范围后 build 在主线上直接制作原生 Text+ 花字和音效；手改过的花字重建时整条保留，支持定稿采纳统计。命令默认演练，带 --apply 才写入。

- 豆包识别（带逐字时间；极速版或 2.0 标准版，Key 开通了哪种用哪种，极速版没开通自动改用标准版），本机声学对齐逐字定位。
- AI 通读全文选重录、删废话；有文案时据此选版本、纠正字幕同音错字、提示漏念，字幕以现场原话为准。删口吃、重复字时程序核对删的是重复的那一份，每条短删改打印删后读作什么；AI 选错了、用户还没在页面上改过时，`edit_project.py reopen` 回退（旧版挪进备份）再 `finish`，不用另开 `_第2次` 目录。
- 默认句内不剪、句与句之间紧剪；数字词（如“X500”）自动按整词计时。
- 在审片台“口播剪辑”栏用改文字的方法剪口播（逐字删改、拖小句调顺序），一键“保存到达芬奇”（2026-11-22 起原来单独的审核网页并进审片台）；达芬奇里只留一条最新主线，旧版自动收进“历史版本”文件夹。
- 花字音效（thinkcut-edit「花字音效」）：按标记或风格手册自动加花字和音效，字被人挡时自动抠人压字，给一条短审片时间线审核；按反馈迭代。
- 不渲染，由人在达芬奇检查后手动导出。

限制：只支持单音轨、恒定 29.97 fps（分辨率按原片，竖屏可用）；每台 Mac 需本机安装声学对齐环境、ffmpeg、Node 和达芬奇 MCP：`bash bin/setup_mac.sh` 一键检查并补装，`python3 bin/doctor.py --summary` 查看中文自检。

## 目录

| 路径 | 内容 |
|---|---|
| `bin/` | 入口：`edit_project.py`（新片全流程）、`transcribe_video.py`（仅转写）、`setup_mac.sh`（新 Mac 一键准备）、`doctor.py`（自检）、`run_tests.sh`（离线回归）、`doubao_credentials.py`（配置 Key） |
| `engine/` | 剪辑引擎、文字剪辑的后台（页面在 thinkcut-edit 的审片台“口播剪辑”栏）、字幕与达芬奇交付 |
| `skills/` | Codex Skill（各 Mac 通过符号链接指向这里） |
| `secrets/` | 豆包 Key，不进存档 |

## 豆包 Key（管理员）

Key 须来自[豆包语音新版控制台](https://console.volcengine.com/speech/new/)，火山方舟 Key 不能用。在一台 Mac 运行 `python3 bin/doubao_credentials.py`，在仅本机可访问的页面输入；配置或轮换后用 `bin/doubao_direct_asr.py --audio <短音频> --output <个人目录内新 JSON>` 实测一次，通过后才会被使用。Key 在 NAS 上是明文，能读这个文件的人都能用它产生费用，所以 NAS 共享权限只给自己人。不要把 Key 粘贴到聊天、命令或文档里。极速版单次限 2 小时、100 MB。

## 更新后

改了 Skill 或程序后，各 Mac 的链接自动指向新文件；但已开着的 AI 对话不会重新读取，需新开对话，并重启正在运行的审核服务。
