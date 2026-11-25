#!/usr/bin/env bash
# sync_env.sh 的离线测试（2026-11-21 体检）：用临时目录造假的 NAS 原件和 Mac 副本，不碰真的环境、不碰回收站。
#   bash bin/test_sync_env.sh
set -u
export THINKCUT_ALIGN_ENV=thinkcut-align THINKCUT_MATTING_ENV=thinkcut-matting      # 假环境用新名字；不让 bin/legacy_names.sh 换成旧名字
HERE=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P) || exit 1
T=$(mktemp -d) || exit 1; trap 'rm -rf "$T"' EXIT
fails=0
check() { if [[ $1 == 0 ]]; then echo "通过 $2"; else echo "失败 $2"; fails=$((fails+1)); fi; }
mk() {  # 造一份假环境：$1 = 目录
  local d=$1 v
  mkdir -p "$d/python/cpython-3.12.12-macos-aarch64-none" "$d/davinci-resolve-mcp"
  echo '{ "version": "4.8.20" }' > "$d/davinci-resolve-mcp/package.json"
  for v in thinkcut-align thinkcut-matting/venv davinci-resolve-mcp/venv; do
    mkdir -p "$d/$v/lib/python3.12/site-packages/torch-2.10.0.dist-info" "$d/$v/lib/python3.12/site-packages/torch"
    echo x > "$d/$v/lib/python3.12/site-packages/torch/__init__.py"
  done
}
run() { THINKCUT_ENV_NAS="$T/nas" THINKCUT_ENV_LOCAL="$T/mac" bash "$HERE/sync_env.sh" "$@" 2>&1; }

mk "$T/nas"; mk "$T/mac"
out=$(run --check); code=$?
[[ $code == 0 && "$out" == *"环境一致"* ]]; check $? "两边装的包一样时 --check 说一致、返回 0"

mkdir -p "$T/mac/thinkcut-align/lib/python3.12/site-packages/新装的包-1.0.dist-info"
out=$(run --check); code=$?
[[ $code == 4 && "$out" == *"只在 Mac 副本：thinkcut-align: 新装的包-1.0"* ]]; check $? "Mac 上多装了一个包时 --check 说不一致、指出是哪个包、返回 4"
rm -rf "$T/mac/thinkcut-align/lib/python3.12/site-packages/新装的包-1.0.dist-info"

rm -rf "$T/nas/thinkcut-matting/venv/lib/python3.12/site-packages/torch-2.10.0.dist-info"; mkdir -p "$T/nas/thinkcut-matting/venv/lib/python3.12/site-packages/torch-2.14.1.dist-info"
out=$(run --check); code=$?
[[ $code == 4 && "$out" == *"只在 NAS 原件：thinkcut-matting/venv: torch-2.14.1"* && "$out" == *"只在 Mac 副本：thinkcut-matting/venv: torch-2.10.0"* ]]; check $? "两边版本不同时 --check 两边都列出来"
rm -rf "$T/nas" "$T/mac"; mk "$T/nas"; mk "$T/mac"

echo 本机新装 > "$T/mac/thinkcut-align/lib/python3.12/site-packages/torch/local_new_pkg.py"
out=$(run); code=$?
[[ $code == 3 && "$out" == *"会从 Mac 运行副本里删掉 1 个"* && "$out" == *"local_new_pkg.py"* && -f "$T/mac/thinkcut-align/lib/python3.12/site-packages/torch/local_new_pkg.py" ]]; check $? "NAS → Mac 会删 Mac 上多出来的文件时先列出来停下（返回 3），文件还在"
[[ ! -f "$T/mac/版本戳.txt" ]]; check $? "停下时没有写版本戳"
rm "$T/mac/thinkcut-align/lib/python3.12/site-packages/torch/local_new_pkg.py"

mkdir -p "$T/mac/thinkcut-align/lib/python3.12/site-packages/torch/__pycache__"; echo c > "$T/mac/thinkcut-align/lib/python3.12/site-packages/torch/__pycache__/x.cpython-312.pyc"; echo d > "$T/mac/python/.DS_Store"
out=$(run); code=$?
[[ $code != 3 && "$out" != *"会从 Mac 运行副本里删掉"* ]]; check $? "只多出运行时自己生成的 __pycache__、.pyc、.DS_Store 时不拦"

# 推送（Mac → NAS）：替换的旧文件挪进回收站备份，深层子文件夹里的也行（2026-11-22 第一次真推送时备份目录缺子文件夹中途停下）
rm -rf "$T/nas" "$T/mac"; mk "$T/nas"; mk "$T/mac"
mkdir -p "$T/mac/thinkcut-align/lib/python3.12/site-packages/silero_vad/data"; echo 新 > "$T/mac/thinkcut-align/lib/python3.12/site-packages/silero_vad/data/model.jit"
echo 新版 > "$T/mac/thinkcut-align/lib/python3.12/site-packages/torch/__init__.py"
mkdir -p "$T/mac/thinkcut-align/lib/python3.12/site-packages/silero_vad-6.2.3.dist-info"      # 真装包一定会多出版本信息，推送靠它认出这一块变了
RC="$T/回收站"; out=$(THINKCUT_ENV_NAS="$T/nas" THINKCUT_ENV_LOCAL="$T/mac" THINKCUT_RECYCLE_ROOT="$RC" bash "$HERE/sync_env.sh" --push 2>&1); code=$?
[[ $code == 0 && -f "$T/nas/thinkcut-align/lib/python3.12/site-packages/silero_vad/data/model.jit" && "$(cat "$T/nas/thinkcut-align/lib/python3.12/site-packages/torch/__init__.py")" == 新版 ]]; check $? "推送：新装的包和改过的文件都到了 NAS 原件（深层子文件夹也行）"
old=$(find "$RC" -path '*thinkcut-align/lib/python3.12/site-packages/torch/__init__.py' 2>/dev/null | head -1)
[[ -n "$old" && "$(cat "$old")" == x ]]; check $? "推送：被替换的旧文件挪进了回收站的备份里，没永久丢"
[[ -z "$(find "$RC" -type d -empty 2>/dev/null)" ]]; check $? "推送：回收站备份里没有留下空文件夹"

out=$(THINKCUT_ENV_NAS="$T/nas" THINKCUT_ENV_LOCAL="$T/mac" THINKCUT_RECYCLE_ROOT="$RC" bash "$HERE/sync_env.sh" --push 2>&1); code=$?
[[ $code == 0 && "$out" == *"python 两边一样，不推"* && "$out" == *"没有要推的"* ]]; check $? "推送：两边装的包一样的块不推（Python 解释器文件夹里的链接 NAS 存不了，整块推会报错）"

out=$(run --chek); code=$?
[[ $code == 2 ]]; check $? "打错参数返回 2、不同步"

out=$(THINKCUT_ENV_NAS="$T/没有" THINKCUT_ENV_LOCAL="$T/mac" bash "$HERE/sync_env.sh" --check 2>&1); code=$?
[[ $code == 1 && "$out" == *"读不到 NAS"* ]]; check $? "NAS 没挂上时 --check 说读不到原件"

[[ $fails == 0 ]] && echo "全部测试通过" || echo "有 $fails 项未通过"
exit $fails
