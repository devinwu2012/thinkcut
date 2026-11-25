#!/usr/bin/env bash
set -eu
ROOT=$(cd "$(dirname "$0")/.." && pwd -P)
mkdir -p "$HOME/.local/bin" "$HOME/.codex/skills" "$HOME/.claude/skills"
link_one() {
  target="$1"
  dest="$2"
  if [ -L "$dest" ] && [ "$(readlink "$dest")" = "$target" ]; then return; fi
  if [ -L "$dest" ] && [ ! -e "$dest" ]; then rm "$dest"; echo "已替换失效链接 $dest"; fi   # 旧 NAS 路径留下的死链接（2026-10-24）
  if [ -e "$dest" ] || [ -L "$dest" ]; then echo "停止：已有其他文件，不覆盖 $dest" >&2; exit 1; fi
  ln -s "$target" "$dest"
}
chmod +x "$ROOT/bin/yxer_secure.py"
link_one "$ROOT/bin/yxer_secure.py" "$HOME/.local/bin/yxer-safe"
# 2026-10-24 用户要求上传拆回单独技能：链接 thinkcut-project 和 thinkcut-upload 两个入口
for home in "$HOME/.codex" "$HOME/.claude"; do
  link_one "$ROOT/skills/thinkcut-project" "$home/skills/thinkcut-project"
  link_one "$ROOT/skills/thinkcut-upload" "$home/skills/thinkcut-upload"
done
echo 'thinkcut-project、thinkcut-upload 已链接到 Codex 与 Claude。新 Mac 需本机单独设置钥匙串：yxer-safe key set'
