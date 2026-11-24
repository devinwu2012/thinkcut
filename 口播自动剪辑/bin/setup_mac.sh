#!/usr/bin/env bash
# 接入新 Mac：检查依赖，把 Skill 链接给 Claude 和 Codex。不出片、不碰达芬奇工程。
set -u
ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P) || exit 1
SKILL="$ROOT/skill/thinkcut-edit"; failed=0
say() { printf '%s：%s\n' "$1" "$2"; }

if command -v python3 >/dev/null 2>&1; then say Python "已就绪（$(python3 --version 2>&1)）"; else say Python '缺少，请从 https://www.python.org/downloads/macos/ 安装后重跑'; exit 1; fi
if python3 -c 'import PIL, numpy' 2>/dev/null; then say Pillow/numpy 已就绪
else
    say Pillow/numpy '缺少，正在安装到当前用户'
    python3 -m pip install --user pillow numpy >/dev/null 2>&1 && python3 -c 'import PIL, numpy' && say Pillow/numpy 已安装 || { say Pillow/numpy '安装失败，请把这段输出发给 AI'; failed=1; }
fi
for dir in "$HOME/.claude/skills" "$HOME/.codex/skills"; do
    mkdir -p "$dir" && ln -sfn "$SKILL" "$dir/thinkcut-edit" && say "Skill 链接" "$dir/thinkcut-edit → $SKILL" || failed=1
done
echo "--- 其余依赖（ffmpeg、Node、Chrome、达芬奇、NAS 音效库、a-roll、导航 Skill）："
PYTHONUTF8=1 python3 "$SKILL/scripts/thinkcut_edit.py" doctor || failed=1
[[ $failed == 0 ]] && echo "全部就绪" || echo "有未就绪的项目，见上面"
exit $failed
