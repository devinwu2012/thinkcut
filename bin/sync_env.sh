#!/usr/bin/env bash
# 程序环境：原件在 NAS 上程序目录的 环境/；macOS 27.2 起系统不准运行 NAS 上的程序（2026-10-29 实测），
# 所以 Mac 上放一份运行副本。副本可以随时删，运行本脚本就从 NAS 原样恢复。
#   bash bin/sync_env.sh          # NAS → Mac（只拷有变化的）
#   bash bin/sync_env.sh --push   # Mac → NAS（在 Mac 上重装或升级了环境后，更新 NAS 原件）
#   bash bin/sync_env.sh --check  # 只比对，不拷不删：NAS 原件和 Mac 副本装的包、版本是否一致（一致返回 0，不一致返回 4）
#   NAS → Mac 会删掉 Mac 副本里 NAS 原件没有的文件时，也先列出来停下，确认后加 --yes；同步完验证关键包能导入，并写 版本戳.txt（2026-11-21 体检）
#       会删掉 NAS 原件里 Mac 副本没有的文件时，先列出来停下；确认后加 --yes。被删、被替换的旧文件挪进 NAS 回收站，不永久删（2026-11-13 审查）
# 对齐模型 环境/huggingface 和 Node 模块 环境/node_modules 只是数据，程序直接读 NAS，不拷。
set -u
ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P) || exit 1
NAS="${THINKCUT_ENV_NAS:-$ROOT/环境}"                                         # 两个 THINKCUT_ENV_* 只给离线测试换成临时目录用
# 2026-11-24：环境原件只有使用版那一份；在开发版（使用版文件夹名后面加 -开发）里跑时用使用版的
[[ -z "${THINKCUT_ENV_NAS:-}" && "$ROOT" == *-开发 && ! -d "$NAS" ]] && NAS="${ROOT%-开发}/环境"
[[ -f "$ROOT/bin/旧名兼容.sh" ]] && source "$ROOT/bin/旧名兼容.sh"     # 从改名前升级来的机器：环境文件夹还叫旧名字
ALIGN=${THINKCUT_ALIGN_ENV:-thinkcut-align}; MATTING=${THINKCUT_MATTING_ENV:-thinkcut-matting}
LOCAL="${THINKCUT_ENV_LOCAL:-$HOME/Library/Application Support/${THINKCUT_APP_DIR:-ThinkCut}/环境}"
ITEMS=(python "$ALIGN" "$MATTING" davinci-resolve-mcp)
VENVS=("$ALIGN" "$MATTING/venv" davinci-resolve-mcp/venv)
src=$NAS; dst=$LOCAL; push=0; yes=0; check=0
keys() {  # 每个环境里程序真正要用的包：同步后逐个导入验证（以前只验 Python 能启动，缺包查不出）
  case "$1" in
    "$ALIGN") echo "torch qwen_asr" ;;
    "$MATTING/venv") echo "torch torchvision numpy PIL" ;;
    davinci-resolve-mcp/venv) echo "mcp" ;;
  esac
}
fingerprint() {  # $1 = 环境目录；列出 Python 版本、每个环境装的包和版本、达芬奇连接程序版本，一行一项
  local side=$1 v sp
  ls "$side/python" 2>/dev/null | LC_ALL=C sort | sed 's/^/python: /'
  for v in "${VENVS[@]}"; do
    sp=$(ls -d "$side/$v"/lib/python*/site-packages 2>/dev/null | head -1)
    [[ -n "$sp" ]] || { echo "$v: 没有装包的目录"; continue; }
    ls "$sp" | sed -n 's/\.dist-info$//p' | LC_ALL=C sort | sed "s#^#$v: #"
  done
  sed -n 's/^ *"version": *"\([^"]*\)".*/davinci-resolve-mcp: 程序版本 \1/p' "$side/davinci-resolve-mcp/package.json" 2>/dev/null | head -1
}
for a in "$@"; do
  case "$a" in
    --push) push=1 ;;
    --yes) yes=1 ;;
    --check) check=1 ;;
    *) echo "不认识的参数：${a}（只有 --push、--yes、--check；什么都不写是 NAS → Mac）"; exit 2 ;;   # 打错参数不再当成默认方向反着同步
  esac
done
if [[ $check == 1 ]]; then
  [[ -d "$NAS/python" ]] || { echo "读不到 NAS 上的环境原件：${NAS}（NAS 挂上了吗）"; exit 1; }
  [[ -d "$LOCAL/python" ]] || { echo "这台 Mac 上还没有环境运行副本：运行 bash bin/sync_env.sh"; exit 4; }
  a=$(fingerprint "$NAS"); b=$(fingerprint "$LOCAL")
  if [[ "$a" == "$b" ]]; then
    echo "环境一致：NAS 原件和 Mac 运行副本装的包、版本相同（共 $(echo "$a" | wc -l | tr -d ' ') 项）"
    [[ -f "$LOCAL/版本戳.txt" ]] && echo "  Mac 副本$(head -1 "$LOCAL/版本戳.txt")"
    exit 0
  fi
  echo "环境不一致：NAS 原件和 Mac 运行副本装的包不一样（先列 20 条）："
  diff <(echo "$a") <(echo "$b") | sed -n -e 's/^< /  只在 NAS 原件：/p' -e 's/^> /  只在 Mac 副本：/p' | head -20
  echo "在这台 Mac 上新装或升级过 → bash bin/sync_env.sh --push；想让 Mac 回到 NAS 原件的样子 → bash bin/sync_env.sh"
  exit 4
fi
[[ $push == 1 ]] && { src=$LOCAL; dst=$NAS; }
[[ -d "$src/python" ]] || { echo "没有环境原件：$src"; exit 1; }
if [[ $push == 1 ]]; then
  for x in "${ITEMS[@]}"; do [[ -d "$src/$x" ]] || { echo "Mac 副本不完整（缺 ${x}），不能用它更新 NAS 原件：$src"; exit 1; }; done
  # 2026-11-22：只推两边装的包确实不一样的那几块（按 --check 的比对）。Python 解释器文件夹里有很多链接，NAS 存不了，整块推会在备份链接时报错停下；没变的就不推
  part() { case "$1" in python) echo 'python: ';; "$MATTING") echo "$MATTING/venv: ";; davinci-resolve-mcp) echo 'davinci-resolve-mcp';; *) echo "$1: ";; esac; }
  fa=$(fingerprint "$src"); fb=$(fingerprint "$dst"); keep=()
  for x in "${ITEMS[@]}"; do k=$(part "$x"); [[ "$(grep -F "$k" <<<"$fa")" == "$(grep -F "$k" <<<"$fb")" ]] && echo "  $x 两边一样，不推" || keep+=("$x"); done
  [[ ${#keep[@]} -gt 0 ]] || { echo "NAS 原件和 Mac 副本装的包一样，没有要推的"; exit 0; }
  ITEMS=("${keep[@]}")
  gone=$(for x in "${ITEMS[@]}"; do rsync -a --delete --exclude logs -n -v "$src/$x/" "$dst/$x/" 2>/dev/null | sed -n "s#^\*\{0,1\}deleting #$x/#p"; done)
  if [[ -n "$gone" && $yes == 0 ]]; then
    echo "这次更新会从 NAS 原件里去掉 $(echo "$gone" | wc -l | tr -d ' ') 个 Mac 副本没有的文件（先列 20 个）："; echo "$gone" | head -20 | sed 's/^/  /'
    echo "确认 Mac 副本是完整的、就是要这样，再运行：bash bin/sync_env.sh --push --yes（旧文件会挪进 NAS 回收站）"; exit 3
  fi
  bak="${THINKCUT_RECYCLE_ROOT:-$(dirname "$ROOT")/#recycle/$(basename "${ROOT%-开发}")}/环境_$(date +%Y%m%d_%H%M%S)"      # THINKCUT_RECYCLE_ROOT 只给离线测试换成临时目录用
  # 系统自带的 rsync 带 --backup-dir 时 --delete 不生效（2026-11-13 实测）：多出来的文件这里自己挪进回收站，下面 rsync 只管新增和替换
  if [[ -n "$gone" ]]; then
    while IFS= read -r g; do
      [[ "$g" == */ ]] && continue
      mkdir -p "$bak/$(dirname "$g")" && mv "$dst/$g" "$bak/$g" || { echo "挪进回收站失败：$g"; exit 1; }
    done <<< "$gone"
    echo "$gone" | sort -r | while IFS= read -r g; do [[ "$g" == */ ]] && rmdir "$dst/$g" 2>/dev/null; done
    echo "  $(echo "$gone" | grep -vc '/$') 个多出来的文件已挪进 $bak"
  fi
fi
if [[ $push == 0 && -d "$dst" ]]; then
  # 2026-11-21 体检：NAS → Mac 以前直接删 Mac 副本里多出来的文件，在 Mac 上新装还没 --push 的包会被悄悄抹掉。现在先列出来停下。
  # 运行时自己生成的 __pycache__、.pyc、.DS_Store 不算（删了会再生成）。
  gone=$(for x in "${ITEMS[@]}"; do [[ -d "$dst/$x" ]] || continue; rsync -a --delete --exclude logs -n -v "$src/$x/" "$dst/$x/" 2>/dev/null | sed -n "s#^\*\{0,1\}deleting #$x/#p"; done | grep -v -e '__pycache__' -e '\.pyc$' -e '\.DS_Store$' -e '/$')
  if [[ -n "$gone" && $yes == 0 ]]; then
    echo "这次同步会从 Mac 运行副本里删掉 $(echo "$gone" | wc -l | tr -d ' ') 个 NAS 原件没有的文件（先列 20 个）："; echo "$gone" | head -20 | sed 's/^/  /'
    echo "如果是在这台 Mac 上新装或升级的东西，先运行 bash bin/sync_env.sh --push 存回 NAS；确认可以删，再运行 bash bin/sync_env.sh --yes"; exit 3
  fi
fi
mkdir -p "$dst" || exit 1
echo "同步 $src → ${dst}（第一次约 3 GB，NAS 小文件多，可能要十几分钟）"
for x in "${ITEMS[@]}"; do
  echo "  $x"
  if [[ $push == 1 ]]; then
    # 2026-11-22 第一次真推送时查出：系统自带 rsync 往 NAS 的回收站备份被替换的旧文件时，不会自己建子文件夹，报 No such file or directory 中途停下。
    # 先列出这次真要替换／新建的文件和链接（-n -i 只演练），把它们在备份目录里的上级文件夹建好；推完删掉备份目录里空着的文件夹。
    rsync -a -n -i --no-links --exclude logs "$src/$x/" "$dst/$x/" 2>/dev/null | sed -n 's/^[<>c.]f[^ ]* //p' \
      | while IFS= read -r f; do dirname "$f"; done | LC_ALL=C sort -u | while IFS= read -r p; do mkdir -p "${bak:?}/$x/$p"; done
    # 符号链接不推（--no-links）：NAS 存不了真链接，往回收站备份链接会报错停下；链接是 Python 自带的快捷方式，装包不会变，NAS → Mac 时另有一步还原
    rsync -a --no-links --exclude logs --backup --backup-dir="$bak/$x" "$src/$x/" "$dst/$x/" 2>&1 | grep -v '^skipping non-regular file'; [[ ${PIPESTATUS[0]} == 0 ]]
  else rsync -a --delete --exclude logs "$src/$x/" "$dst/$x/"; fi || { echo "同步 $x 失败"; exit 1; }
done
# NAS 把符号链接存成 1067 字节的 XSym 小文件，拷回 Mac 时不一定还原成链接（2026-10-29 重新挂载后实测）：这里还原
if [[ "$dst" == "$LOCAL" ]]; then
  python3 - "$dst" <<'PY' || { echo "还原链接失败"; exit 1; }
import os, sys
n = 0
for top, dirs, files in os.walk(sys.argv[1]):
    for f in files:
        p = os.path.join(top, f)
        if os.path.islink(p) or os.path.getsize(p) != 1067: continue
        with open(p, 'rb') as h: head = h.read(1067).split(b'\n')
        if head[0] != b'XSym' or len(head) < 4: continue
        target = head[3][:int(head[1])].decode('utf-8')
        os.remove(p); os.symlink(target, p); n += 1
print(f'  还原了 {n} 个链接')
PY
fi
# 虚拟环境里记着 Python 本体的绝对位置，按目标位置改写（可执行文件的链接本来就是相对的）
for v in "${VENVS[@]}"; do
  cfg="$dst/$v/pyvenv.cfg"; [[ -f "$cfg" ]] || continue
  ver=$(sed -n 's/^version[_info]* = \([0-9]*\.[0-9]*\).*/\1/p' "$cfg" | head -1)
  py=$(cd "$dst/python" && ls -d cpython-"$ver".*-macos-aarch64-none | head -1)
  [[ -n "$py" ]] || { echo "没有 Python ${ver}：$dst/python"; exit 1; }
  sed -i '' -e "s#^home = .*#home = $dst/python/$py/bin#" -e "s#^executable = .*#executable = $dst/python/$py/bin/python$ver#" "$cfg"
done
for v in "${VENVS[@]}"; do
  [[ "$dst" == "$NAS" ]] && { echo "  · $v 是原件（NAS 上不运行，系统不让运行 NAS 上的程序）"; continue; }
  "$dst/$v/bin/python" -c "import sys" 2>/dev/null && echo "  ✓ $v 能启动" || { echo "  ✗ $v 启动失败"; exit 1; }
  mods=$(keys "$v")
  bad=$("$dst/$v/bin/python" -c 'import importlib, sys
bad = []
for m in sys.argv[1:]:
    try: importlib.import_module(m)
    except Exception as e: bad.append(m + "（" + type(e).__name__ + "）")
print("、".join(bad))' $mods 2>/dev/null)
  [[ -z "$bad" ]] && echo "  ✓ $v 关键包能导入（${mods}）" || { echo "  ✗ $v 关键包导入失败：${bad}。环境不完整：在一台好的 Mac 上运行 bash bin/sync_env.sh --push 更新 NAS 原件后再同步"; exit 1; }
done
{ echo "同步于 $(date '+%F %T')，$([[ $push == 1 ]] && echo 'Mac → NAS' || echo 'NAS → Mac')，主机 $(hostname -s)"; fingerprint "$dst"; } > "$dst/版本戳.txt"
[[ $push == 1 && -d "${bak:-}" ]] && find "${bak:?}" -type d -empty -delete 2>/dev/null      # 预先建的备份文件夹里没用上的空文件夹清掉
echo 完成
