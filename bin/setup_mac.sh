#!/usr/bin/env bash
# ThinkCut 接入一台 Mac：把这里的 5 个技能入口（thinkcut-edit、thinkcut-project、thinkcut-cover、thinkcut-upload、thinkcut-feedback）链接给 Claude 和 Codex，
# 再跑各项目自检（字幕和花字用 macOS 自带的冬青黑体，不用另装字体）。
#   bash bin/setup_mac.sh           # 链接技能＋装上传工具＋恢复环境运行副本＋自检
#   bash bin/setup_mac.sh --links   # 只链接技能（发版后 bin/release.py 自动跑这一步）
set -u
ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P) || exit 1
# 2026-11-24：技能只链接使用版。在开发版（文件夹名以 -开发 结尾）跑会把用户做片子用的技能指到开发中的程序上，所以停下；使用版位置见 bin/locations.py
if [[ "$ROOT" == *-开发 ]]; then echo "这是开发版（${ROOT}），技能只链接使用版：请运行 bash \"$(python3 "$ROOT/bin/locations.py" prod "$ROOT" || echo '<使用版>')/bin/setup_mac.sh\""; exit 2; fi
only_links=0
for a in "$@"; do case "$a" in --links) only_links=1 ;; *) echo "未知参数：$a"; exit 2 ;; esac; done
failed=0
link() {  # 链接名 源目录
  local name=$1 src=$2
  [[ -f "$src/SKILL.md" ]] || { echo "缺技能：$src"; failed=1; return; }
  for dir in "$HOME/.claude/skills" "$HOME/.codex/skills"; do
    mkdir -p "$dir"
    if [[ -e "$dir/$name" && ! -L "$dir/$name" ]]; then echo "已有同名文件夹，未覆盖：$dir/$name"; failed=1; continue; fi
    ln -sfn "$src" "$dir/$name" && echo "链接 $dir/$name → $src" || failed=1
  done
}
# 5 个技能入口（10-23 合并成 3 个，10-24 上传拆回独立入口，11-11 加反馈）；剪口播、B-roll、花字、导航是 thinkcut-edit 下面的步骤
link thinkcut-edit     "$ROOT/studio/skill/thinkcut-edit"      # 剪辑：口播→动画→字幕→导航→花字→导出
link thinkcut-project  "$ROOT/projects/skills/thinkcut-project"  # 项目：建项目、整理素材、存档、登记封面
link thinkcut-cover    "$ROOT/cover/skill/thinkcut-cover"             # 封面
link thinkcut-upload   "$ROOT/projects/skills/thinkcut-upload"   # 上传：蚁小二内部草稿（2026-10-24 拆回独立入口）
link thinkcut-feedback "$ROOT/studio/skill/thinkcut-feedback"  # 反馈：问题清单→反馈单交维护者（2026-11-11）
# 从改名前升级来的安装：旧技能名照样链上（链到仓库里的旧路径，退回旧版本也不断），用户和 AI 原来的叫法还能用
if [[ -f "$ROOT/bin/legacy_names.sh" ]]; then
  source "$ROOT/bin/legacy_names.sh"
  for pair in ${THINKCUT_LEGACY_SKILLS:-}; do link "${pair%%=*}" "$ROOT/${pair#*=}"; done
fi
if [[ $only_links == 1 ]]; then [[ $failed == 0 ]] && echo "技能链接就绪" || echo "有技能没链上，见上面"; exit $failed; fi
echo "--- 上传用的 yxer-safe（只存蚁小二草稿、拦发布命令；Key 要在这台 Mac 上单独设：yxer-safe key set）"
bash "$ROOT/projects/bin/setup_mac.sh" || failed=1      # 2026-11-13 审查：以前总安装脚本不装它，新 Mac 上传时报找不到 yxer-safe
echo "--- 程序环境：从 NAS 的 环境/ 恢复 Mac 运行副本（macOS 27.2 不准运行 NAS 上的程序）"
bash "$ROOT/bin/sync_env.sh" || failed=1
echo "--- 动画/字幕/审核线自检"
PYTHONUTF8=1 python3 "$ROOT/studio/skill/thinkcut-edit/scripts/thinkcut_edit.py" doctor || failed=1
[[ $failed == 0 ]] && echo "全部就绪" || echo "有未就绪的项目，见上面"
exit $failed
