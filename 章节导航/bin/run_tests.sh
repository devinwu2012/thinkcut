#!/bin/bash
# 章节导航离线测试（2026-11-13 纳入 git 时加）
# 测试用自己的配置档（“测试IP”当默认 IP），不靠哪个真 IP 的配置（bin/测试配置档.py，2026-11-24）
if [ -z "${THINKCUT_PROFILES:-}" ]; then P=$(mktemp -d) || exit 1; trap 'rm -rf "$P"' EXIT; THINKCUT_PROFILES=$(python3 "$(dirname "$0")/../../bin/测试配置档.py" "$P") || exit 1; export THINKCUT_PROFILES; fi
cd "$(dirname "$0")/../scripts" && python3 -m pytest -q -p no:cacheprovider test_navigation.py && echo "全部测试通过"
