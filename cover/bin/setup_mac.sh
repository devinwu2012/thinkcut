#!/usr/bin/env bash
# 接入新 Mac：检查依赖，把 Skill 链接给 Claude 和 Codex。不生图、不消耗额度。
set -u
ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P) || exit 1
SKILL="$ROOT/skill/thinkcut-cover"; failed=0
say() { printf '%s：%s\n' "$1" "$2"; }

if command -v python3 >/dev/null 2>&1; then say Python "已就绪（$(python3 --version 2>&1)）"; else say Python '缺少，请从 https://www.python.org/downloads/macos/ 安装后重跑'; exit 1; fi
if python3 -c 'import PIL, numpy' 2>/dev/null; then say Pillow/numpy 已就绪
else
    say Pillow/numpy '缺少，正在安装到当前用户'
    python3 -m pip install --user pillow numpy >/dev/null 2>&1 && python3 -c 'import PIL, numpy' && say Pillow/numpy 已安装 || { say Pillow/numpy '安装失败，请把这段输出发给 AI'; failed=1; }
fi
if [[ -f /System/Library/Fonts/Supplemental/Songti.ttc ]]; then say 宋体 已就绪; else say 宋体 '缺少 Songti.ttc（macOS 自带），请在"字体册"里下载"宋体-简"'; failed=1; fi
if [[ -f "/System/Library/Fonts/Hiragino Sans GB.ttc" ]]; then say 冬青黑体 已就绪; else say 冬青黑体 '缺少 Hiragino Sans GB.ttc（macOS 自带，副标题用），请在"字体册"里下载"冬青黑体简体中文"'; failed=1; fi
if command -v codex >/dev/null 2>&1; then say Codex "已就绪（$(command -v codex)）"; else say Codex '没找到 codex 命令：在 Claude 里出图需要它；装好后本人运行一次 codex login'; failed=1; fi

for dir in "$HOME/.claude/skills" "$HOME/.codex/skills"; do
    mkdir -p "$dir" && ln -sfn "$SKILL" "$dir/thinkcut-cover" && say "Skill 链接" "$dir/thinkcut-cover → $SKILL" || failed=1
done
[[ $failed == 0 ]] && echo "全部就绪" || echo "有未就绪的项目，见上面"
exit $failed
