# ThinkCut · 思刻：给 AI 助手的地图（先读这一页）

这个文件夹是一整套“口播视频剪辑自动化”：撰稿 → 初剪 → 精修（在审片台上改）→ 发布。你（Claude Code 或 Codex）负责操作，人负责看和拍板。所有能力都在这里，只有这一套；做项目（每条视频叫一个项目，以前叫片子）用下面带 `thinkcut-` 前缀的 5 个技能。

## 四条规矩
1. **做项目的会话不改程序和说明。** 发现程序或说明要改，用 thinkcut-feedback 技能整理成反馈单，交给维护者；自己不动程序、技能说明和问题台账。做项目时往程序目录里放数据不算改：反馈单、素材库索引、用户存的导航样式预设（`chapters/配置/`）。
2. **本机不存项目的东西。** 每个项目的东西都在它自己的项目文件夹；达芬奇工程在项目的 `02_制作/达芬奇工程`（项目自己的库），不用本机库和别的库。Mac 上只留程序本身、技能链接、达芬奇库列表登记、环境运行副本（可删，`bash bin/sync_env.sh` 恢复）、审片台按空格小服务（可删，下次自动重建；见 审片工作台.md）、以及**用完即删的临时文件**（抠像的中间帧、渲染缓存这类写完就删的文件放系统临时目录，用完自动删、下次启动清残留；成品、可复用的结果、日志、报告照旧写回项目文件夹）。
3. **两道闸只有人能放行**：整片导出和正式发布，必须人明确说“可以”；你不替他决定。删数据、对外发消息、付款也一样先问。
4. **按下面“存放规矩”放文件**；新加一类东西就在“要找什么”表里登记一行。

**两条通用做法**
- **给用户看的内容只在对话里说，或放 Claude 文档，不写成 MD 文件**（交付清单、目标、提速建议、方案说明、总结）。只有用户明确要求才写 MD。程序要读的记录照写：`反馈记录.md`、项目文案 `03_成片/<项目名>.md`（定稿文案，和成片放一起）、反馈单、问题台账。项目收尾检查会提醒 `02_制作` 下多出来的给用户看的清单类 MD。
- **默认用满电脑性能**：渲染、自检截图、抽帧、转码、字幕整轨这类耗时处理默认并行；截图类实测 12 路后瓶颈在显卡，不用更多。达芬奇的操作不并行，同一时间只让一个会话写达芬奇。一个项目里不该有单路跑超过 5 分钟的截图或渲染命令，遇到就写进反馈。

## 要找什么 → 去哪

| 要找的 | 去哪 |
|---|---|
| 用户要做某件事，用哪个技能 | 下面“5 个技能入口” |
| 剪辑某一步的规则和做法 | thinkcut-edit 入口的步骤表指到的那份说明 |
| 以前出过什么问题、怎么修的、怎么算修好（thinkcut-feedback 回查要用） | 问题台账 `internal/经验/问题台账.md`（只在内部版里有） |
| 交来还没处理的反馈单 | `internal/经验/待审/`（`_已处理/` 是处理完的；只在内部版里有） |
| 某次具体改了什么 | 根目录 `CHANGELOG.md`（开发记录）、`RELEASE_NOTES.md`（给使用者看的简短版）、`git log -- <文件夹>/` |
| 历史素材、以前用过的镜头 | `python3 core/bin/library.py find "一句口播"`（索引在 `素材库/索引/`） |
| 以前画过什么动画、代码在哪 | `studio/skill/thinkcut-edit/references/动画案例索引.md` |
| 动画风格、配色、标签规矩 | `studio/skill/thinkcut-edit/references/动画风格.md` 开头 |
| 花字、信息卡片样式和组件 | 包装系统 `studio/skill/thinkcut-edit/assets/包装系统/README.md`；达芬奇里的 Text+ 花字见 `core/skills/fx/references/风格手册.md` |
| 人挡字用的开源字体（思源黑体/宋体、Inter）和许可证 | `素材库/字体/`（来源说明 `来源说明.txt`）；风格库程序和说明在包装系统 `rendang.py`、`README.md` |
| 音效文件和用法 | `素材库/音效素材/`（来源说明 `来源说明.txt`）；用法登记 `core/engine/config/sfx_favorites.json` |
| 某个项目的东西（原片、成片、复盘、反馈记录） | 那个项目的项目文件夹，不在这里 |
| 跑全套离线测试、查代码 | `python3 bin/test_all.py`（和发版时跑的同一套；`python3 bin/test_all.py cover` 只跑一块）；`uvx ruff@0.17.6 check .`（只查真错误，配置 `ruff.toml`）；要装的 Python 包见 `requirements/`；缺字体 `bash bin/fetch_fonts.sh`；GitHub 自动测试 `.github/workflows/test.yml` |
| 审片台后台怎么调本机 AI（Claude Code／Codex）、设置页、后台 AI 的钥匙代办 | `studio/skill/thinkcut-edit/references/审片工作台.md`「AI 引擎」；程序 `scripts/thinkcut/engine.py`、`settings.py`、`bin/thinkcut_broker.py` |
| 首页、审片台的 AI 对话侧栏（⌘K）：范围、权限、接到项目、只认页面上点的 | `studio/skill/thinkcut-edit/references/审片工作台.md`「AI 对话侧栏」；程序 `scripts/thinkcut/chat.py`、页面片段 `assets/对话/对话.html` |
| 新 Mac 或链接坏了 | `bash bin/setup_mac.sh`（链接 5 个技能、恢复环境运行副本、自检） |
| Mac 程序外壳（ThinkCut：一个窗口，首页和项目在里面来回走） | 构建 `bash bin/build_app.sh`（`--install` 放进应用程序；在开发用的程序文件夹里构建出的是“ThinkCut 开发版”：另一个程序标识、橙色“开发”角标、首页端口 8762）；外壳代码 `bin/app/`，起停首页的判断 `bin/thinkcut_app.py`（测试 `bin/test_thinkcut_app.py`） |

## 5 个技能入口

| 技能 | 用户会这么说 | 入口文件 |
|---|---|---|
| **thinkcut-edit 剪辑** | 剪口播、配 B-roll、做动画/成片、加花字音效、做导航条、导出、收尾 | `studio/skill/thinkcut-edit/SKILL.md` |
| **thinkcut-project 项目** | 开个项目、整理素材、存档、登记封面 | `projects/skills/thinkcut-project/SKILL.md` |
| **thinkcut-upload 上传** | 上传、传到蚁小二、存草稿 | `projects/skills/thinkcut-upload/SKILL.md` |
| **thinkcut-cover 封面** | 做封面 | `cover/skill/thinkcut-cover/SKILL.md` |
| **thinkcut-feedback 反馈** | 反馈、总结这次的问题、收尾、复盘这条片子、复盘这个项目 | `studio/skill/thinkcut-feedback/SKILL.md` |

一个项目的顺序：thinkcut-project 建项目 → thinkcut-edit 剪口播 → 动画、字幕、导航、花字音效、审核线、导出 4K → thinkcut-cover 封面 → thinkcut-upload 上传草稿 → thinkcut-feedback 收尾反馈。

## 文件夹是干什么的（名字是历史留下的，看用途别看名字）

整个文件夹是**一个 git 仓库**：五块是里面的普通文件夹，一个版本号（根目录 `VERSION`）、一份更新记录（根目录 `CHANGELOG.md`）。

| 文件夹 | 用途 | 存档 |
|---|---|---|
| `studio/` | thinkcut-edit 总入口、第 3 步动画成片（动画、字幕、音效、审核线、自检、导出）、包装系统、thinkcut-feedback | git |
| `core/` | thinkcut-edit 第 1 步剪口播（`skills/a-roll`）、第 2 步配 B-roll（`skills/b-roll`）、第 4 步达芬奇花字音效（`skills/fx`）、素材库程序（`bin/library.py`） | git |
| `chapters/` | thinkcut-edit 第 5 步章节导航条；样式预设在 `配置/` | git |
| `projects/` | thinkcut-project、thinkcut-upload | git |
| `cover/` | thinkcut-cover | git |
| `素材库/` | `音效素材/`、`索引/`（跨期素材库数据）、`字体/`（人挡字用的开源字体） | 数据，不进 git |
| `B-roll学习库/` | 只剩 B-roll 的台账索引 `台账索引.json`（审核线靠它找回主线台账，不能删；文件夹名是历史留下的） | 数据，不进 git |
| `环境/` | 程序环境原件（Python、对齐、抠人、达芬奇连接、模型、字幕组件）；Mac 上用运行副本，`bash bin/sync_env.sh` 同步，`--check` 只比对 | 数据，不进 git |
| `bin/` | 新 Mac 安装 `setup_mac.sh`、环境同步 `sync_env.sh`、Mac 程序外壳 `build_app.sh`（代码在 `bin/app/`，产物在 `build/`、不进 git）等小工具 | git |
| 根目录 `AGENTS.md`、`README.md`、`CLAUDE.md`、`VERSION`、`CHANGELOG.md` | 地图、使用说明、给 Claude 的入口、全家的版本号和更新记录 | git |
| 根目录 `README.en.md`、`RELEASE_NOTES.md`、`LICENSE.md`、`THIRD_PARTY_NOTICES.md`、`CONTRIBUTING.md`、`SECURITY.md` | 英文简介、给使用者的更新说明、许可证、第三方组件、参与和安全问题说明（公开版都带） | git |
| `internal/` | 维护这个程序的人用的开发记录（交接、规矩、问题台账、反馈单）；做项目不用读 | git（内部版） |
| 根目录旧名软链接 `口播剪辑`、`口播自动剪辑`、`视频项目管理`、`封面`（及更早的一个旧封面目录名）、`章节导航`、`配置档`、`内部` | 2026-11-25 前的文件夹名，分别指向 `core`、`studio`、`projects`、`cover`、`chapters`、`profiles`、`internal`；老项目里写死的路径（约 1500 处）、用户已有的技能链接靠它们继续能打开。**暂留、勿删**：用户定一直留到老项目都归档；新写的路径一律用英文名 | git（内部版；公开版不带） |

## 存放规矩（新文件按这个放）

1. **每类东西只放一处**：上表已有的类别放进对应位置；新类别先在本页两张表里各登记一行再放。
2. **全局的放全局，某一步的放那一步**：总说明管整个程序；只管某一步的规则和程序放那一步的文件夹。
3. **说明只写规则**：写成能照做的句子，出处留一句（日期、项目名、最短原话）；长的经过进 `CHANGELOG.md` 或问题台账。
4. **程序和说明进 git，数据不进**：素材、索引、学习库、环境、项目产物都不进代码存档。
5. **项目的产物放项目文件夹**：动画代码、视频、复盘、反馈记录都在项目 `02_制作/` 下，不放这里。
6. **不要的东西不永久删**：先挪进回收站，删之前问用户。

字体默认都用系统自带的，不另装：字幕和封面副标题冬青黑体，花字和包装系统苹方。**例外**：人挡字风格库用 `素材库/字体/` 里的开源字体（思源黑体/宋体、Inter，OFL；程序按文件路径加载，不装进系统）。

---
维护这个程序本身的人另见 [internal/维护者规矩.md](internal/维护者规矩.md)。开发版和使用版分开放：开发版、分支（worktree）、试验片子都在使用版上一级的 `Dev/ThinkCut/` 里（开发版 `ThinkCut-开发/`、分支 `分支/`、试验片子 `试验项目/`），两份怎么互相找见 `bin/locations.py`。
