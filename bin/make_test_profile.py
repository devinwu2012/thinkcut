#!/usr/bin/env python3
"""离线测试用的配置档（2026-11-24）：五块的测试不靠哪个真 IP 的配置，换谁的配置档、导出的干净版里都一样能跑。
复制 profiles/公共.json 和 示例IP/，再加一个“测试IP”当默认 IP（值和抽配置前写死在程序里的一样：蓝色封面、小何、3:4满屏、全部 6 个平台、装修类领域词）。
  python3 bin/make_test_profile.py <空文件夹>      # 建好，打印这个文件夹；测试脚本 export THINKCUT_PROFILES=<它>
各块的 run_tests.sh、projects/bin/conftest.py 在没设 THINKCUT_PROFILES 时自动用它。"""
import json
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TEST_IP = {'名字': '测试IP', '剪辑': {'画幅': '3:4满屏'}, '配音': {'音色': 'zh_female_xiaohe_uranus_bigtts', '音色名': '小何'},
           '封面': {'标题色': [38, 84, 160], '副标题字色': [250, 244, 236], '主角色相': 215, '圈箭头色': [226, 84, 44], '标题横向压缩': 0.88},
           '发布': {'youtube分类': '26', '原创': True, '领域词': ['装修', '家装', '家居', '房', '设计', '避坑', '知识', '科普', '干货']}}


def make(dest):
    dest = Path(dest); dest.mkdir(parents=True, exist_ok=True)
    pub = json.loads((ROOT / 'profiles' / '公共.json').read_text(encoding='utf-8')); pub['默认IP'] = '测试IP'
    (dest / '公共.json').write_text(json.dumps(pub, ensure_ascii=False, indent=1), encoding='utf-8')
    if not (dest / '示例IP').exists(): shutil.copytree(ROOT / 'profiles' / '示例IP', dest / '示例IP')
    (dest / '测试IP').mkdir(exist_ok=True)
    (dest / '测试IP' / 'ip.json').write_text(json.dumps(TEST_IP, ensure_ascii=False, indent=1), encoding='utf-8')
    (dest / '测试IP' / '规矩.md').write_text('# 测试IP 的规矩（离线测试用）\n', encoding='utf-8')
    return dest


if __name__ == '__main__':
    print(make(sys.argv[1]))
