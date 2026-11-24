# ThinkCut · 口播台

**English summary.** ThinkCut is an AI-operated workflow for talking-head ("口播") videos on macOS. You bring a script or a recording; your own AI coding assistant (Claude Code or Codex, signed in with your own account) drafts the cut, animations, subtitles, chapter bar, sound effects, cover and publishing copy, and puts everything on a local review page where you edit and approve. Editing happens in DaVinci Resolve Studio. The program has no built-in model and never touches your AI account. Two gates always need your explicit OK: exporting the full video and publishing it. This is an early version, verified so far only in its author's own setup, and the documentation is in Chinese.

---

## 这是什么

口播台帮你把**一条口播视频，从文案做到成片**。你负责想法、审片和拍板；剪辑里重复、费时的活，交给你自己电脑上的 AI 助手去做。

它不是一个自带 AI 的软件。你需要自己开一个 AI 编程助手（Claude Code 或 Codex），让它在这个文件夹里干活；程序本身不内置任何模型，也不经手你的 AI 账号。

### 一条片子怎么走（四个阶段）

1. **写文案**：在审片台的“文案”栏里写或贴口播稿，和 AI 一起改。AI 只给修改建议，你点“采纳”才会真的改进文案。
2. **出初稿**：文案定稿、点“开工”后，AI 一口气做出一版初稿：剪口播（选重录、去口误和废话、校对字幕）、配画面（缺的先用生成图占位）、卡点动画、花字和音效、章节导航条、达芬奇里的审核时间线、封面、发布用的标题和简介。中间不来问你；缺口播就先用机器配音占位。
3. **在审片台上改**：每条片子有一个只在你本机打开的网页，叫“审片台”。AI 做出来的东西都发到这里，你看、点“可以”、选方案、直接在文字上提意见；点“交给 AI”后它按你的意思改，改完再发回来。看片本身仍在达芬奇里。
4. **发布**：导出成片、整理标题简介标签、存成发布工具里的草稿。

### 什么事谁做

| AI 做 | 你做 |
|---|---|
| 转写、选重录、剪口、对字幕 | 录口播、给文案和想法 |
| 做动画、花字、音效、章节条、封面 | 在审片台看、选、提意见 |
| 在达芬奇里搭时间线 | 在达芬奇里最后看一遍 |
| 整理标题、简介、标签，存发布草稿 | **整片导出前确认；点正式发布** |

### 两道闸

下面两件事，**必须由你明确说“可以”，AI 不会替你决定**：
- **整片导出**：你在审片台点了审核线的“这一栏定了”之后，才会导出 4K 成片。
- **正式发布**：程序只会把成片存成发布工具里的**草稿**，并且拦截发布命令；点“发布”永远是你自己在客户端里做。

删除你的数据、对外发消息、付款这类事也一样，AI 会先问你。

## 要准备什么

- **一台 Mac**。（芯片、系统版本的最低要求：待确认。作者在 Apple 芯片的 Mac 上使用。）
- **DaVinci Resolve Studio（达芬奇付费版）**，从 Blackmagic 官网自己安装并打开过一次。程序用到了只有 Studio 才有的功能（脚本接口、人声隔离等），免费版不行。作者测试用的是 Studio 21.1.1；更早或更晚的版本是否可用：待确认。
- **一个 AI 编程助手**：Claude Code 或 Codex，用你自己的账号登录。封面的底图目前通过 Codex 的生图功能出；只用 Claude Code 时封面这一步怎么做：待确认。
- **豆包语音的账号和 Key**：口播的文字识别用它（需要来自豆包语音新版控制台的 Key；火山方舟的 Key 不能用）。识别是收费接口，会产生少量费用。机器配音占位也用同一个账号。
- **发布工具（可选）**：程序目前对接的是“蚁小二”：把成片、封面、标题简介存成各平台的草稿。不用发布这一步可以不装。
- 命令行里的基础工具：Xcode 命令行工具、Homebrew，其余（ffmpeg、Node、Python 3.12、声学对齐模型等）由安装脚本检查并补装。

## 怎么装

先把整个文件夹放到你的电脑上，在终端里进入它，然后：

```bash
bash bin/setup_mac.sh
```

它会：把 5 个技能（thinkcut-edit、thinkcut-project、thinkcut-cover、thinkcut-upload、thinkcut-feedback）链接给 Claude Code 和 Codex、检查并恢复程序环境、最后跑一遍体检。缺什么它会直接告诉你。只链技能用 `bash bin/setup_mac.sh --links`。单独体检可以用：

```bash
python3 口播自动剪辑/skill/thinkcut-edit/scripts/thinkcut_edit.py doctor
```

> **现在的限制（2026-11-24 在一份新导出的副本里照着走过一遍）：这还不是一个能一键装好的版本。**
> - 能照做走通的：链接 5 个技能（`bash bin/setup_mac.sh --links`）；建自己的 IP 配置、建项目文件夹；离线测试（见文末，前提是先放好下面说的字体和音效）；封面排版（要有底图）；装成 Mac 程序。
> - 走不通的：`bash bin/setup_mac.sh` 的“程序环境”一步会停在“没有环境原件：<程序目录>/环境”。`环境/`（Python 3.12、声学对齐环境、抠人环境、达芬奇连接程序）是作者自己装好的一份，不在代码里。`bash 口播剪辑/bin/setup_mac.sh` 不带参数时也会先去恢复这份环境、恢复不了就停下；带 `--check` 只列缺什么（对齐依赖、对齐模型约 1.2 GB、达芬奇 MCP 和字幕组件、fontTools、抠人环境和模型）。从零把这些装起来的步骤还没有整理、也没人在别的电脑上验证过。所以**转文字、剪口播放进达芬奇、自动抠人**这几步，在你的电脑上目前用不了。
> - 不在代码里、要自己准备的：`素材库/字体/`（人挡字用的开源字体：思源黑体 SourceHanSansSC-Heavy/Light.otf、思源宋体 SourceHanSerifSC-Heavy.otf、Inter 的 InterDisplay-*.ttf 放 `Inter/` 子文件夹，均 SIL OFL）；`素材库/音效素材/`（作者自己的音效库，不随代码分发；没有它花字音效这一步找不到音效）。
> - 其余“待确认”的项见上面“要准备什么”。

豆包 Key 的设置（在本机网页里输入，不会出现在命令或聊天里）：

```bash
python3 口播剪辑/bin/doubao_credentials.py
```

要用发布工具时，在本机的钥匙串里存它的 Key（输入时不回显）：

```bash
yxer-safe key set
```

### 装成 Mac 程序（口播台）

不想每次开浏览器、记网址的话，可以把“首页”（所有片子都在上面）装成一个程序，放在程序坞里，双击就打开：一个窗口，首页上双击一条片子就在这个窗口里进到它，按 ⌘W 退回首页（一次只开一条片子）：

```bash
bash bin/build_app.sh --install
```

它会做出一个叫 ThinkCut 的程序，放进“应用程序”文件夹。**第一次打开要在“应用程序”里右键它 →“打开”**：这个程序没有苹果的签名，macOS 会先问一下，点“打开”就行，以后直接双击。想放进程序坞，打开后在程序坞的图标上右键 →“选项”→“在程序坞中保留”。

- 在首页按 ⌘W 关掉窗口也不会退出：按 ⌘0（菜单“窗口 → 全部片子”）或点程序坞图标叫回来；按 ⌘Q 才退出。退出时程序自己开的首页会一起停，各条片子的审片台不受影响。
- 常用快捷键：⌘N 新建片子、⌘W 从片子退回首页（在首页时关窗口）、⌘0 回首页、⌘R 重新载入。
- 程序里不带任何 AI，也不碰你的账号：AI 还是你自己的 Claude Code 或 Codex 连进来。
- 程序记住了是从哪个文件夹做出来的。把整个文件夹挪了地方，就在新位置再跑一次上面那条命令。
- 打不开时，窗口里会直接说原因和日志在哪。

## 怎么做第一条片子

0. 先建你自己的 IP 配置（颜色、字体、配音音色、发布平台、给 AI 看的口吻规矩都在里面）：`python3 bin/cutdesk_config.py new 你的名字`，再把 `配置档/公共.json` 的“默认IP”改成它（或写进不进存档的 `配置档/本机.json`）。不建的话用自带的 `示例IP`。
1. 在 Claude Code 或 Codex 里打开这个文件夹，让它先读根目录的 [AGENTS.md](AGENTS.md)（给 AI 的地图和规矩；Claude Code 会自动从 [CLAUDE.md](CLAUDE.md) 指过去）。
2. 对 AI 说：“开个项目，名字叫 XXX”。它会用下面这条命令建好一个标准的项目文件夹（原始素材、制作、成片、达芬奇归档都有固定位置）：

   ```bash
   python3 视频项目管理/bin/project.py init "项目名"
   ```

   项目默认建在程序文件夹旁边的 `<IP 名>视频` 文件夹里（`配置档/公共.json` 的“片子文件夹”，每个 IP 可以在自己的 `ip.json` 里改）；想放别处，加 `--root <你的位置>`。
3. 把口播录像放进项目的素材文件夹，或者只给一个题目和文案。对 AI 说“剪一下这条片子”。
4. 打开审片台，写文案、看初稿、提意见、点“可以”：

   ```bash
   python3 口播自动剪辑/skill/thinkcut-edit/scripts/thinkcut_edit.py workbench <项目文件夹>
   ```

5. 做到一半换了会话、想知道进度和下一步：

   ```bash
   python3 口播自动剪辑/skill/thinkcut-edit/scripts/thinkcut_edit.py status <片子目录>
   ```

6. 你确认后，AI 导出 4K、存发布草稿；最后你自己在发布工具里点发布。


## 你的数据放在哪

- **每条片子的东西都在它自己的项目文件夹里**：原片、成片、封面、文案、达芬奇工程、制作过程、反馈记录。达芬奇工程建在项目自己的库里，不用你电脑上的默认库。
- **程序自己的东西**在这个文件夹里：程序、技能说明、素材库（音效、字体、索引）。
- **Mac 上还会留的**：5 个技能链接、程序环境的运行副本（可以删，用 `bash bin/sync_env.sh` 恢复）、审片台的小服务缓存（可以删），以及用完就删的临时文件。
- **程序不会动**：你的原始素材文件；你在达芬奇里手改过的片段、字幕、花字、音效（手改过的就是定稿，不会被批量覆盖）；你没点头的任何导出或发布。没有批量删除项目的命令。

### 密钥怎么放

密钥**不进仓库、不写进文档、不贴进聊天**：
- 豆包 Key 放在程序文件夹里的 `口播剪辑/secrets/`，已被版本管理忽略。
- 发布工具的 Key 放在这台 Mac 的登录钥匙串里。钥匙串读不到时程序停下，不会退回明文。
- 不要把任何 Key 粘贴给 AI。

## 文件夹一览

| 文件夹 | 管什么 |
|---|---|
| `口播自动剪辑/` | 总入口、动画成片、字幕、音效、审核线、导出、审片台 |
| `口播剪辑/` | 剪口播、配画面、达芬奇里的花字音效、素材库工具 |
| `章节导航/` | 章节导航条 |
| `封面/` | 封面 |
| `视频项目管理/` | 建项目、整理素材、存档、上传草稿 |
| `bin/` | 安装和环境小工具、配置档读法 `cutdesk_config.py` |
| `配置档/` | 每个 IP 一套配置：`公共.json` 是共用的默认值，`<IP>/ip.json`、`<IP>/规矩.md` 是这个 IP 自己的 |

各文件夹自己的说明在它们的 `README.md` 里；给 AI 看的完整地图在 [AGENTS.md](AGENTS.md)。

## 现状

这是一个**早期版本**：目前只在作者自己的电脑和环境里验证过，没有在别人的机器上装过，上面“怎么装”里的限制就是证据之一。作者已经用它做过多条片子，但细节还在边用边改。遇到问题，欢迎把现象和你的环境一起反馈。

可以自己跑的离线测试（不连达芬奇、不开浏览器、不生图）：

```bash
bash 口播自动剪辑/bin/run_tests.sh
```
