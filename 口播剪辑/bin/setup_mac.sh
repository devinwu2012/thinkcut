#!/bin/bash
# 新 Mac 一键准备 AI 口播剪辑环境。每一步先检查，已就绪就跳过；不碰视频、项目和达芬奇工程。
#   bash bin/setup_mac.sh            # 安装缺的部分（默认用国内镜像）
#   bash bin/setup_mac.sh --check    # 只检查，不安装
#   bash bin/setup_mac.sh --no-mirror
set -u
root="$(cd "$(dirname "$0")/.." && pwd -P)"
# 2026-10-29：环境原件在 NAS 上程序目录的 环境/；macOS 27.2 不准运行 NAS 上的程序，Mac 上用运行副本（先在使用版程序目录跑 bin/sync_env.sh 恢复；
# 这里新装或升级了环境，跑 sync_env.sh --push 更新 NAS 原件）。对齐模型是数据，直接放 NAS。
nas_env="$(cd "$root/.." && pwd -P)/环境"
[ -f "$root/../bin/旧名兼容.sh" ] && source "$root/../bin/旧名兼容.sh"     # 从改名前升级来的机器：环境文件夹还叫旧名字
env_dir="$HOME/Library/Application Support/${THINKCUT_APP_DIR:-ThinkCut}/环境"
check=0; mirror=1
for arg in "$@"; do
  case "$arg" in --check) check=1;; --no-mirror) mirror=0;; *) echo "未知参数：$arg"; exit 2;; esac
done
missing=()
ok() { echo "  ✓ $1"; }
need() { echo "  ✗ $1"; missing+=("$1"); }
# 第一个参数是给人看的项目名，后面是安装命令；--check 时只记缺项，不执行
run() { local label="$1"; shift; if [ $check -eq 1 ]; then need "$label"; return 1; fi; echo "  → 安装：$label"; "$@" || { echo "  ✗ 安装失败：$label"; exit 1; }; }
# 参数先解析完再动手：--check 只检查，不恢复运行副本
if [ ! -d "$env_dir/python" ]; then
  if [ $check -eq 1 ]; then need "环境的 Mac 运行副本（运行 bash $root/../bin/sync_env.sh 恢复，或去掉 --check 重跑本脚本）"
  else bash "$root/../bin/sync_env.sh" || exit 1; fi
fi
align="$env_dir/${THINKCUT_ALIGN_ENV:-thinkcut-align}"
export HF_HOME="$nas_env/huggingface"
mcp="$env_dir/davinci-resolve-mcp"
mcp_commit=6337d8c
pip_index=(); npm_registry=(); hf_endpoint=()
if [ $mirror -eq 1 ]; then
  pip_index=(-i https://pypi.tuna.tsinghua.edu.cn/simple)
  npm_registry=(--registry https://registry.npmmirror.com)
  hf_endpoint=(env HF_ENDPOINT=https://hf-mirror.com)
fi

echo "== 1. 基础工具"
command -v git >/dev/null && command -v python3 >/dev/null && ok "git / python3" || { need "Xcode 命令行工具（终端运行 xcode-select --install，需要输入密码，请本人操作）"; }
if ! command -v brew >/dev/null; then
  need "Homebrew（需要输入密码，请本人在终端按 https://brew.sh 的一行命令安装，然后重新运行本脚本）"
  echo; echo "请先装好上面缺的部分。"; exit 1
fi
ok "Homebrew"
ffmpeg_has_zscale() { ffmpeg -hide_banner -filters 2>/dev/null | grep -Eq '[[:space:]]zscale[[:space:]]'; }
if command -v ffmpeg >/dev/null && ffmpeg_has_zscale; then ok 'ffmpeg（含 zscale，支持 HDR 转换）'
elif [ "$check" -eq 1 ]; then need 'ffmpeg 缺失或缺 zscale：运行 bash bin/setup_mac.sh 安装/切换 ffmpeg-full'
else
  run 'ffmpeg-full（含 zscale）' brew install ffmpeg-full
  # 普通 ffmpeg 新版不含 zimg；先解除链接，再链接完整版本。
  if brew list --formula ffmpeg >/dev/null 2>&1; then brew unlink ffmpeg || exit 1; fi
  brew link --force ffmpeg-full || exit 1
  ffmpeg_has_zscale && ok 'ffmpeg zscale' || { need 'ffmpeg-full 已安装但当前 ffmpeg 仍缺 zscale，请检查 PATH'; exit 1; }
fi
command -v node >/dev/null && ok 'node' || run 'node' brew install node
command -v python3.12 >/dev/null && ok "python3.12" || run "python3.12" brew install python@3.12

echo "== 2. 声学对齐环境（${align}）"
if "$align/bin/python" -c "import qwen_asr, torch, soundfile" 2>/dev/null; then ok "对齐依赖"
else
  run "对齐依赖" bash -c "python3.12 -m venv '$align' && '$align/bin/pip' install ${pip_index[*]} \
    torch==2.10.0 torchaudio==2.10.0 transformers==4.57.6 accelerate==1.12.0 huggingface_hub==0.36.2 \
    soundfile==0.14.0 numpy==2.3.5 qwen-asr==0.0.6"
fi
# 气口精修的语音检测（2026-11-22）：查不到只提醒，不装、不算缺项；没有它气口精修照旧只按音量判断
if [ -x "$align/bin/python" ] && "$align/bin/python" -c "import silero_vad" 2>/dev/null; then ok "语音检测（silero-vad）"
else echo "  ! 对齐环境里没有 silero-vad：气口精修照旧只按音量判断（能用）；要用语音检测，请架构审查在环境原件里装 silero-vad==6.2.3 后同步"; fi
if [ -x "$align/bin/python" ] && "$align/bin/python" -c "from huggingface_hub import snapshot_download as s; s('Qwen/Qwen3-ForcedAligner-0.6B', local_files_only=True)" 2>/dev/null; then ok "对齐模型已下载"
else
  run "对齐模型（约 1.2 GB）" ${hf_endpoint[@]+"${hf_endpoint[@]}"} "$align/bin/python" -c "from huggingface_hub import snapshot_download as s; s('Qwen/Qwen3-ForcedAligner-0.6B')"
fi

echo "== 3. 达芬奇（需本人从 Blackmagic 官网安装并打开过一次）"
[ -d "/Applications/DaVinci Resolve/DaVinci Resolve.app" ] && ok "DaVinci Resolve" || need "DaVinci Resolve（本人安装）"

echo "== 4. 达芬奇 MCP（${mcp}）"
if [ -f "$mcp/src/server.py" ]; then ok "MCP 程序"
else
  run "MCP 程序" bash -c "git clone https://github.com/samuelgursky/davinci-resolve-mcp.git '$mcp' && git -C '$mcp' checkout $mcp_commit && cd '$mcp' && python3.12 install.py --clients codex --update-policy never"
fi
if [ -d "$mcp/resolve-advanced" ] && (cd "$mcp/resolve-advanced" && node -e "require('./vendor/drp-format/place-subtitles')" 2>/dev/null); then ok "字幕组件（Node 依赖）"
elif [ -d "$mcp/resolve-advanced" ]; then
  run "字幕组件（Node 依赖）" bash -c "cd '$mcp/resolve-advanced' && npm install --omit=dev ${npm_registry[*]}"
else
  need "字幕组件（MCP 里没有 resolve-advanced 目录，请让 Codex 检查 MCP 版本）"
fi

echo "== 5. 共享 Skill"
skill_links_ready=1
skill_home="${CODEX_HOME:-$HOME/.codex}/skills"
for name in a-roll b-roll fx; do   # 2026-10-23 起是 thinkcut-edit 下的步骤：只查说明文件在、入口已链接
  [ -f "$root/skills/$name/SKILL.md" ] || skill_links_ready=0
done
[ -f "$skill_home/thinkcut-edit/SKILL.md" ] || skill_links_ready=0
if [ -e "$skill_home/ai-talking-head-edit" ] || [ -L "$skill_home/ai-talking-head-edit" ]; then
  skill_links_ready=0
fi
if [ "$skill_links_ready" -eq 1 ]; then ok "剪辑入口 thinkcut-edit 已连接（剪口播、B-roll、花字是它下面的步骤）"
else need "剪辑入口 thinkcut-edit 没连上：在使用版程序目录运行 bash bin/setup_mac.sh"; fi

echo "== 6. 花字字体与音效库"
# 花字只用 macOS 自带的苹方 SC（2026-11-12 统一到包装系统，取代冬青黑体）；苹方在系统字体资源目录里
if ls /System/Library/AssetsV2/com_apple_MobileAsset_Font*/*/AssetData/PingFang.ttc >/dev/null 2>&1; then ok "字体 苹方（系统自带）"; else need "字体 苹方（系统里没有，打开字体册下载 PingFang SC）"; fi
if python3 -c 'import fontTools' 2>/dev/null; then ok 'fontTools'
else run 'fontTools' python3 -m pip install --user fonttools; fi
# 音效库和本仓库同在程序目录下：按本仓库在哪儿推，NAS 换了挂载名 也能找到
fx_sfx_dir="${AI_EDIT_SFX_DIR:-$(cd "$root/.." && pwd -P)/素材库/音效素材}"
if [ -d "$fx_sfx_dir" ] && [ -r "$fx_sfx_dir" ] && [ -x "$fx_sfx_dir" ]; then ok '音效库可访问'
else need '音效库不可访问（挂载素材 NAS 或设置 AI_EDIT_SFX_DIR）'; fi

echo "== 7. 自动抠人独立环境与模型"
matting="$env_dir/${THINKCUT_MATTING_ENV:-thinkcut-matting}"
if PYTHONPATH="$root" python3 -c "from engine.fx.matte import environment_check; import sys; sys.exit(not environment_check()[0])" 2>/dev/null; then ok "RVM 环境与模型校验"
else
  run "RVM 环境与模型" python3 "$root/bin/setup_matting.py" ${pip_index[@]+"${pip_index[@]}"}
fi

echo "== 8. 自检"
python3 "$root/bin/doctor.py" --summary
echo
if [ ${#missing[@]} -gt 0 ]; then echo "还缺：${missing[*]}"; exit 1; fi
echo "环境准备完成。"
