#!/bin/bash
# Run every offline regression; exit non-zero if anything fails or is skipped. Never calls paid APIs or Resolve.
set -u
cd "$(dirname "$0")/.." || exit 1
export PYTHONDONTWRITEBYTECODE=1
unset AI_EDIT_RUN_DIR  # a leftover project setting would make tests read a real project
status=0
# 测试不碰真实的达芬奇写入锁：统一换成临时锁文件；跑完核对真实锁文件的内容和修改时间没变。
real_lock="$HOME/.cache/ai-talking-head-edit/resolve-write.lock"
lock_state() { if [ -e "$real_lock" ]; then stat -f '%Fm %z' "$real_lock"; cat "$real_lock"; else echo absent; fi; }
THINKCUT_RESOLVE_LOCK="$(mktemp -t thinkcut-test-resolve-lock)" || exit 1
export THINKCUT_RESOLVE_LOCK
# 测试用自己的配置档（“测试IP”当默认 IP），不靠哪个真 IP 的配置（bin/测试配置档.py，2026-11-24）
if [ -z "${THINKCUT_PROFILES:-}" ]; then test_prof="$(mktemp -d)" && THINKCUT_PROFILES="$(python3 ../bin/测试配置档.py "$test_prof")" || exit 1; export THINKCUT_PROFILES; fi
lock_before="$(lock_state)"
check_python() {  # $1 = group name, $2 = directory
  out=$(cd "$2" && python3 -m unittest discover -p 'test_*.py' 2>&1)
  if ! echo "$out" | grep -q '^OK'; then echo "FAIL $1"; echo "$out" | grep -E '^(FAIL|ERROR):' | head -10; status=1
  elif echo "$out" | grep -q 'skipped='; then echo "FAIL $1：有测试被跳过（缺 ffmpeg 或 node？）"; status=1
  elif ! echo "$out" | grep -qE '^Ran [1-9]'; then echo "FAIL $1：一个测试都没找到"; status=1; fi
}
check_python 'bin 测试' bin
check_python 'engine 测试' engine/src
check_python 'broll 测试' engine/broll
check_python 'fx 测试' engine/fx
check_python '素材库 测试' engine/library
for f in engine/src/test_*.cjs; do
  out=$(node "$f" 2>&1)
  if [ $? -ne 0 ] || ! echo "$out" | grep -q '__ALL_TESTS_DONE__'; then echo "FAIL $f（失败或中途卡住）"; status=1; fi
done
if [ "$(lock_state)" != "$lock_before" ]; then
  echo "FAIL 真实的达芬奇写入锁在测试期间被改动了：$real_lock（测试不该碰它；要是测试时正好有真的写入命令在跑，等它做完重跑一次）"; status=1
fi
rm -f "$THINKCUT_RESOLVE_LOCK"
[ -n "${test_prof:-}" ] && rm -rf "$test_prof"
[ $status -eq 0 ] && echo '全部测试通过' || echo '有测试未通过，不要存档'
exit $status
