#!/usr/bin/env python3
"""给 GitHub 测试机下载苹方 PingFang.ttc（花字、品牌标签要用）。
苹方在 macOS 上是“按需下载”的系统字体：用户的 Mac 用过就有（在 /System/Library/AssetsV2/…/PingFang.ttc），测试机上没有；
测试机上系统用内部的 PingFangUI.ttc（可变字体）顶替同名字体，所以系统的下载接口认为不缺、不会去下（2026-11-26 CI 查到）。
这里照系统的做法：读苹果公开的字体资源目录（mesu.apple.com），找到含 PingFangSC-Regular 的那个资源包，下载、取出 PingFang.ttc、
核对 sha256 和作者 Mac 上那份一致（不一致就停：字形不同，测试里按像素比对会对不上），放到给的文件夹里。
  python3 .github/fetch_pingfang.py <放到哪个文件夹>        # CI 再 sudo 拷到 /Library/Fonts/（程序会在那里找）
"""
import hashlib
import io
import plistlib
import sys
import urllib.request
import zipfile
from pathlib import Path

CATALOG = 'https://mesu.apple.com/assets/macos/com_apple_MobileAsset_Font8/com_apple_MobileAsset_Font8.xml'
WANT = '9ff3ce9439fe285cdabb46f9ceb46b1ac58f1ca07e6f4a764e8286db621a0af9'      # 作者 Mac（macOS 27.2）上的 PingFang.ttc


def get(url):
    with urllib.request.urlopen(url, timeout=300) as r: return r.read()


def main(dest):
    assets = plistlib.loads(get(CATALOG))['Assets']
    hit = [a for a in assets if any(f.get('PostScriptFontName') == 'PingFangSC-Regular' for f in a.get('FontInfo4', []))]
    if not hit: sys.exit('停下：苹果的字体资源目录里没找到苹方（PingFangSC-Regular）')
    a = max(hit, key=lambda a: a.get('_CompatibilityVersion', 0))
    url = a['__BaseURL'] + a['__RelativePath']
    print('下载：' + url, flush=True)
    with zipfile.ZipFile(io.BytesIO(get(url))) as z:
        name = next((n for n in z.namelist() if n.endswith('/PingFang.ttc')), None)
        if not name: sys.exit('停下：资源包里没有 PingFang.ttc')
        data = z.read(name)
    got = hashlib.sha256(data).hexdigest()
    if got != WANT: sys.exit(f'停下：下载的 PingFang.ttc 和作者在用的那份不一样（sha256 {got}），没放进去')
    out = Path(dest); out.mkdir(parents=True, exist_ok=True); (out / 'PingFang.ttc').write_bytes(data)
    print(f'放好：{out / "PingFang.ttc"}（{len(data) // 1048576} MB，指纹一致）')


if __name__ == '__main__':
    if len(sys.argv) != 2: sys.exit(__doc__)
    main(sys.argv[1])
