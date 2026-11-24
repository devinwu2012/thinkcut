# 运行与项目格式

## 依赖

Python 3.9+；FFmpeg（含 libx264、sendcmd、drawbox）；探测原片需要 ffprobe。macOS 使用 Swift/AppKit 和系统 PingFang 字体渲染。其他平台使用 Pillow，并在样式中提供 `font_title_path`、`font_question_path`，可选 TTC 的 `font_title_index`、`font_question_index`。字体需覆盖字幕实际字符并分别提供粗体和中等字重，不接受缺字方框；交付前必须看实际图。字体不随 Skill 分发。

FFmpeg 从 `--ffmpeg`、`FFMPEG` 环境变量、PATH、可用的 imageio_ffmpeg 依次寻找。ffprobe 通过 `--ffprobe`、`FFPROBE`、PATH 寻找。脚本不自动联网安装。缺工具时优先查宿主运行时；安装依赖遵循当前环境授权。探测不可用时可用用户明确提供的最终参数，不能伪造探测结果。

## 命令

以下命令从 Skill 目录运行；其他目录使用脚本绝对路径。路径始终正确引用。

```bash
python3 scripts/navigation.py doctor
python3 scripts/navigation.py preset-show
python3 scripts/navigation.py probe input.mp4
python3 scripts/navigation.py prepare input.srt prepared.json
python3 scripts/navigation.py validate project.json
python3 scripts/navigation.py render project.json output-v1
python3 scripts/navigation.py preset-save my-blue style.json --default
```

`render` 的第二个参数给文件夹名就把成片和过程文件都放进去；给 `xxx.mov`／`xxx.mp4` 就直接出这个文件，过程文件放旁边的 `xxx_导航文件/`（半透明／透明底只能 `.mov`）。

`prepare` 和 `preset-save` 拒绝覆盖目标；`render` 要求不存在的新输出目录。默认预设已存在时更改默认需 Agent 将旧 `config.json` 另存版本后更新，不能用 `preset-save --default` 覆盖它。只保存样式而不设默认可省略 `--default`。

## project.json

示例的 SHA 值需从 `prepare` 的 `source.sha256` 复制。秒值以原片绝对时间为准，结束为不含端点。项目中的字幕路径支持相对于项目文件或绝对路径；导出的快照记录绝对路径便于恢复。

```json
{
  "schema_version": 1,
  "source": {"subtitles": "input.srt", "sha256": "从 prepare 输出取得的真实 SHA256"},
  "target": {"width": 1920, "height": 2560, "nav_height": 280, "fps": "30000/1001"},
  "duration": 60,
  "coverage": [5, 60],
  "subtitle_warnings_reviewed": false,
  "segments": [
    {"chapter": "opening", "title": "开始之前：理清问题", "question": "哪些问题需要先解决？", "start": 5, "end": 20},
    {"chapter": "platform", "title": "第一部分：选择平台", "question": "如何选择适合自己的平台？", "start": 20, "end": 60}
  ]
}
```

- `style` 可直接放完整样式快照；否则读取可选 `preset` 名称，再否则读取用户默认或内置样式。项目快照固定完整 style，不随用户默认变化。
- `source` 允许额外记录原视频路径、大小、mtime、SHA、探测结果和最终设置来源，Agent 继续旧项目时必须核对已记录的原片版本。渲染只消耗字幕，脚本强制验证字幕 SHA。大视频哈希有成本，可先比文件大小/mtime，有改变就重新核对时间。
- `target.height` 是最终画布高度，`nav_height` 是叠加素材高度；都使用正偶数以兼容 H.264/yuv420p。奇数画布需说明编码约束，明确裁切/补边策略，不能悄悄改变用户输出尺寸。
- 帧率用精确分数字符串，29.97 素材先确认是否为 `30000/1001`，不要把数字小数自动等同该分数。
- `coverage` 必须连续，segments 完整覆盖；大章节 id 连续出现，复习独立成章时给独立 id。
- 一条字幕按开始时刻归属板块，时间表写「第一条/最后一条字幕」，不声称是完整句子。边界跨字幕时 Agent 查看上下文确认，可另附完整句意，不能修改原文。
- 源字幕错序会按时间排序；重叠只报警，不改变原时间。人工/Agent 核对后才设置 `subtitle_warnings_reviewed: true`，不要为了运行自动跳过。
- 最终帧边界采用绝对秒 × fps 四舍五入，误差最多半帧；素材放置点也对齐到同一帧网格，使用输出说明的精确时刻。不要逐段累加取整。

## 短标签样式（2026-11-14 定稿，默认）

只要有板块写了 `label`，整条就按短标签样式做：进度条按大章节分段，每段下面写该章节第一个板块的 `label`；`title`、`question` 不上屏。每个大章节的第一个板块都必须有 `label`（可为空字符串；空字符串不做宽度检查，比段间小缝还窄的段也能写，确认单里显示“（空着）”）。

```json
{"chapter": "c1", "title": "第一件：卫生间通风排味", "label": "通风排味", "question": "换气扇为什么排不掉味", "start": 28.3, "end": 55.18}
```

- 字号、线粗、光标和高度都按画布短边（宽和高里小的那个）乘比例算，所以 3:4、9:16、16:9 的 4K 成片上字一样大。字号固定为短边的 `label_ratio`（短边 2160 时 60 像素），不缩小、不截断；标签估算宽度超过这一段的宽度时 `validate` 报错并给出这一段最多几个汉字（按汉字宽度算）。宽度按保守估算（宁大勿小，按 PingFang 粗体实测每类字符最宽值留余量）：汉字、全角标点、emoji 和其它非 ASCII 字符 1 个字号；`W M m w @ %` 1 个；其它大写和英文标点 0.8 个；数字和其它小写 0.65 个；空格 0.35 个。所以中英混排、全大写英文会比真实宽度报得偏大，换短词即可。
- 正在讲的段：标签用 `foreground` 和粗体，未播放的底线用 `track_current`；其他段标签用 `label_dim`。段间小缝 `label_gap_ratio`。
- 红色发光光标（`caret`）跟着进度逐帧滑动（帧号按 `round(t×帧率)` 算，不用 overlay 的 `n`；第 0 帧在条头、最后一帧在条尾，和蓝色进度右沿差不超过 1 像素，`--render` 测试会实测），默认常亮不闪（2026-11-14 用户定）；样式里把 `caret_blink` 设为 true 才会每 1.1 秒暗 0.3 秒。会多出 `caret.png` 过程文件（闪烁时还有 `caret-dim.png`）。
- 高度下限＝标签顶（短边 × `label_top_ratio`）＋一行字高（字号 × 1.45 进位取整）。1.45 是 `navigation.py` 里的 `LABEL_LINE`：PingFang 在 AppKit 里实测行高约 1.40 倍字号，留了余量；`validate`、`text.swift`、Pillow 都用这一个数，所以用 PingFang 时 `validate` 通过就不会到 `render` 才报高度不足（Pillow 换了别的字体仍以 `render` 的实际检查为准）。短边 2160 时下限 211，取偶数 212。新片子导航条高 224，老片子 340；`nav_height` 比内容高时内容上下居中，低于下限时 `validate` 报错。
- `label` 算进文案确认的指纹，改了以前的确认就不算了。没确认不拦 `render`（2026-11-22 用户定“先做出来再改”）：只提醒“初稿·未确认”，`validation.json` 的 `wording_confirmed` 记 false。
- `confirm` 在标准片子上只认审片台（`<片子>/02_制作/自动剪辑/审片工作台/` 的 `审片台.json`、`标记.json`）上用户点的“可以”／“这一栏定了”，规则见 SKILL.md 第 6 步（2026-11-22 261122 反馈单 新1）。`章节文案确认.json` 多记两项：`basis`（依据，如“审片台：用户对导航文字栏 导1 第 2 版点了可以（时间）”）、`labels`（确认时的每段短标签）。
- **字多自动压窄**（2026-11-21 新19）：标签比这一段宽时，字横向压窄（字高、字号不变），不再直接报错。下限是 style 的 `label_squeeze_min`（默认 0.75，最窄压到 75%；写 1 就是不压窄，放不下直接报错）。压到下限还放不下才报错，并告诉你压到最窄这一段最多几个汉字。压窄时标签两侧给邻段留 `squeeze_pad` 的空隙（短边 2160 时 12 像素）。`validate` 按保守估算判断，真正压多少 `render` 时按实测宽度算；确认单里会标“（会压窄到 N%）”，“最多几个字”仍是不压窄的数。放得下的标签一点不变。
- **导航条的底**（2026-11-21 新19，style 的 `background_mode`）：
  - `opaque`（不写就是它）：不透明黑底，出 `navigation-full.mp4`。老项目、老片子都是它，一个像素不变。
  - `dim`：半透明深色底（45% 黑），没播到的标签字色提亮到 `#C4C4C4`，所有标签垫一层暗影，白字在亮画面上也看得清。出 `navigation-full.mov`（ProRes 4444 带透明通道）。261117 用户要“不要黑底”，本片最后用的就是这种。新片子默认（`thinkcut-edit` 的 `init` 把 `nav_bg: dim` 写进 job.json）。
  - `clear`：完全透明底，字色 `#E6E6E6`、暗影加重。**亮色画面（白底、白墙）上白字几乎看不清**，只适合画面稳定偏暗的片子；用之前要在片子最亮的画面上抽帧看。
  - 只支持短标签样式；两行文字的旧样式只能不透明黑底。`background_alpha`、`shadow_alpha`、`label_dim` 等细项由 `background_mode` 一并给定，改细项请另存成新的 style 预设。
  - 条子后面是什么：这个条是叠在上层的整帧透明片。透明处露出下面的 B-roll（动画镜头）或口播画面，不是黑的；是否还露一截口播画面看 B-roll 有没有铺到条子下面。

## 产物与检查

`navigation-full.mp4`（半透明／透明底时是 `navigation-full.mov`）、`project.json`、`timeline.json`、`时间轴.md`、`使用说明.md`、编号文字 PNG、`validation.json`。其余 render-spec、filters、progress、encode.log 为可复现过程记录。

编码先生成静态章节文字，再按真实帧时间驱动进度宽度；章节间隙按大章节时长占比放置。进度区间为 coverage，最后一帧填满。默认黑底蓝色，文本不随进度闪动。

脚本完整解码检查帧数。Agent 另外用 ffprobe 核对尺寸、帧率、音轨（应无音轨），或者查看实际解码日志；抽帧时按帧号选择，核对最长文字、每个章节边界前后及首尾进度。`validation.json` 的 `visual_review_required` 表示还需人工/Agent 视觉检查，编码成功不能替代该步骤。

预览只在用户需要时额外生成并明确标为预览；交付主链接始终是正常速度完整版。适配画面需把条带放到最终画布中检查，而非仅孤立看导航图。

## 自主适配补充

`question` 是兼容字段名，可存当前问题、话题或步骤；空字符串表示只有第一行。每个板块的标题仍必须非空。字幕不足以定位的板块需 `visual_evidence: [{"time": 12.5, "note": "实际看到的操作或场景"}]`，时刻必须位于该板块。整片无对白时 `source: {}` 可用于纯画面分章；应额外记录原视频路径、大小与 mtime，首尾字幕字段为 null，时间表明确显示无对白。不得为通过校验而伪造画面依据。

`source.video` 配合 `video_size`、`video_mtime_ns` 可验证原片版本；变化后先重核，再更新快照。

使用 `doctor` 检查宿主能力。macOS 需要可调用的 Swift/AppKit；其他环境或没有 Swift 时，需要 Pillow 和两种字重的字体文件。在 style 中明确字体路径及 TTC index，字体不随 Skill 分发。字体覆盖范围与许可由使用者核对；实际查看输出才能确认无缺字。

渲染通过 `-vf` 直接传递滤镜，兼容取消旧 filter_script 参数的 FFmpeg。编码要求 libx264、sendcmd 和 drawbox；音频提取需要 aresample。脚本不会自动下载依赖。

音频提取与分段合并命令见 [转写与时间映射](transcription.md)。使用外部转写时记录服务或模型、语言、时间基准和上传授权；这些记录不包含凭证。
