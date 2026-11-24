# B-roll 发布版模块

timeline/ledger/reconcile/arrange 是纯数据函数；candidates/matching/placement 与 `broll.index`、`broll.media` 串联候选、索引、补充描述、匹配和生成。service 通过 resolve_adapter/writer 负责实时读写、补偿、撤销和 CLI；离线测试使用假 SDK，不连接达芬奇或 NAS，不替代实机验收。

## 数据约定

- 所有录制帧都是时间线绝对帧，范围 `[start,end)`；`duration=end-start`。
- `normalize_snapshot` 接受 thinkcut-edit「花字音效」 的 timeline dict、含 timeline dict 的外层快照，或任务夹具的 tracks dict；返回 tracks/items 列表。
- items 与 `fx.snapshot.read_timeline` 对齐；额外字段放在 `broll_extra`：`file_size`、`left_offset`、`right_offset`、`color`、`aspect` 等。props 保留变换/音量。照片的 offset 为空；夹具的源出点仅作原始证据，V1 映射按录制时长构造，非线性/重复源帧报错。
- 轨道 enabled 不可信，不参与可见性/对账；使用片段 enabled。字幕是通用名时调用方须先解析 DRT，补 `subtitle_text`。
- 候选 catalog 为 `{segment: {segment,file,usable_start,usable_end,focus?,subject?,people?,quality?,aspect?,black_bars?}}`；可用区间是时间线帧域，T2/T3 换算原片帧率。
- 匹配 `segments=[{spot,lines:[起始行,终止行],candidates:[segment或{segment,source_start}],shots?:[{end,candidates}]}]`；行号从1开始；最多3镜/3候选，内部镜尾必须为V1跳切点。`face=[{lines:[起始行,终止行]}]`。
- `arrange` 不修改输入；placements 含录制范围、原片源入点、segment、parent_segment、track、status、中文reason；`preserved` 保留原AI读回，`removed_ids` 仅列明确点名且精确未手改的项。`flashes` 是本次相关闪缝，非空时不得交付；无关原有闪缝在 `existing_flashes` 汇报。覆盖下降超过15个百分点通过 `needs_coverage_confirmation` 提示。新 spot 不得与台账重名；多镜派生编号也检查碰撞。
- `reconcile.diff_snapshots`（对账）接受前后快照及台账；返回手改动作、原始差异、A-roll差异、结果表和下一次用的 `ledger`。不确定超20%时旧台账不推进。排布用的偏好是固定的 `arrange.default_profile`（学偏好的功能 2026-11-21 下线，见 `docs/历史做法.md`）。

## 明确的实现选择

- 4段停用V1均精确保留（3段章节＋1段照片下方口播），不按证据摘要误减为3段。
- 起止优先在5帧容差内吸附V1跳切点；可向内裁到合格跳切点，不拉长到句尾。无合格跳切点才使用其他合法边界。
- 身份至少两个独立信号：文件名和大小合算来源信号，源入点、独立片段名前缀可作第二项；默认片段名等于文件名时不重复计数。唯一前缀与源入点也可识别缺媒体信息的副本。旧标签前缀包含主选/备选说明，避免同一个D标签误匹配两个片段。用户同源小段按源入点/位置一对一分配。
- 停用、裁短、删除、上层用户替换在对账时保存口播源保留位；缺失的停用备选或原声音频不产生保留位。所有 spot 基线随口播波纹剪辑换算，口播源已删设终态；不确定 spot 不覆盖确定结果。
- 目录裁后 ID 只标可用区间，过滤、复用和台账按 parent_segment；写入另存原片源入点/路径，生成片段的余量偏移不用于同源间隔比较。索引按文件身份复用，保留本次清单之外的描述；明确 null 表示已看过。

## 验证

`PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s engine/broll/live -p 'test_*.py'`

`bash bin/run_tests.sh` 从 engine/broll 自动发现此包，无需修改测试入口。

对账回归（口播波纹/花字/复制/DRP改ID和路径都不算手改等）见 test_reconcile.py；T4 编号复现及次要回归见 test_t4_review.py / test_t4_minor.py，接口回归见 test_service_live.py。
