#!/usr/bin/env bash
# 把人挡字用的开源字体下载到 素材库/字体/（思源黑体 SC、思源宋体 SC、Inter Display；都是 SIL OFL，可免费商用）。
# 用法：bash bin/fetch_fonts.sh            已经有、而且和作者那份一模一样的就跳过
# 下载的压缩包放 FONT_CACHE（默认本机临时目录下 thinkcut-fonts/，约 250 MB），下次不再下；CI 用它做缓存。
# 每个字体都核对 sha256，和作者在用的那份不一样就停下（字形不同，测试里逐像素比对会对不上）。
set -euo pipefail
ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)
DEST="$ROOT/素材库/字体"
CACHE="${FONT_CACHE:-${TMPDIR:-/tmp}/thinkcut-fonts}"
mkdir -p "$DEST/Inter" "$CACHE"

# 压缩包名|下载地址
ZIPS=(
  "09_SourceHanSansSC.zip|https://github.com/adobe-fonts/source-han-sans/releases/download/2.005R/09_SourceHanSansSC.zip"
  "09_SourceHanSerifSC.zip|https://github.com/adobe-fonts/source-han-serif/releases/download/2.003R/09_SourceHanSerifSC.zip"
  "Inter-4.1.zip|https://github.com/rsms/inter/releases/download/v4.1/Inter-4.1.zip"
)
# 放到哪（相对 素材库/字体/）|从哪个压缩包取|sha256
FONTS=(
  "SourceHanSansSC-Heavy.otf|09_SourceHanSansSC.zip|88c749b0a54a0800124ded6544e399302ed224aa49992ea364b88769f825c54c"
  "SourceHanSansSC-Light.otf|09_SourceHanSansSC.zip|c406ec4c795fe71ec443a8a55570d7df53dea560ea93d4117eadab6e2b19680d"
  "SourceHanSerifSC-Heavy.otf|09_SourceHanSerifSC.zip|053e911fdb1a55d4d8512a1f11203f1fb9a7291e34bc260474b8c80358c69fa6"
  "Inter/InterDisplay-Black.ttf|Inter-4.1.zip|25460b0d5b3afd9764d63cf838f94145af624f8d4c9d20e0f74116be42878b32"
  "Inter/InterDisplay-Light.ttf|Inter-4.1.zip|1d6072bc7807fa7bf08a6d273368f04ac68e77438258f32975057a61c8772811"
)

sha() { shasum -a 256 "$1" | cut -d' ' -f1; }
url_of() { local z; for z in "${ZIPS[@]}"; do [[ "${z%%|*}" == "$1" ]] && { echo "${z#*|}"; return; }; done; }

for f in "${FONTS[@]}"; do
  IFS='|' read -r rel zip want <<<"$f"
  out="$DEST/$rel"
  if [[ -f "$out" && "$(sha "$out")" == "$want" ]]; then echo "已有：${rel}"; continue; fi
  if [[ ! -s "$CACHE/$zip" ]]; then
    echo "下载：${zip}"
    curl -fL --retry 3 -o "$CACHE/$zip.part" "$(url_of "$zip")"
    mv "$CACHE/$zip.part" "$CACHE/$zip"
  fi
  name=$(basename "$rel")
  tmp=$(mktemp -d)
  unzip -q -j -o "$CACHE/$zip" "*${name}" -d "$tmp" || { echo "停下：${zip} 里没找到 ${name}"; rm -rf "$tmp"; exit 1; }
  got=$(sha "$tmp/$name")
  if [[ "$got" != "$want" ]]; then
    echo "停下：${zip} 里的 ${name} 和作者在用的那份不一样（sha256 ${got}），没放进去"; rm -rf "$tmp"; exit 1
  fi
  mv "$tmp/$name" "$out"; rm -rf "$tmp"
  echo "放好：${rel}"
done
# 许可证一起放（有就放，没有不算错）
for z in "09_SourceHanSansSC.zip|思源黑体_LICENSE.txt" "09_SourceHanSerifSC.zip|思源宋体_LICENSE.txt" "Inter-4.1.zip|Inter/LICENSE.txt"; do
  IFS='|' read -r zip lic <<<"$z"
  [[ -f "$DEST/$lic" || ! -s "$CACHE/$zip" ]] && continue
  tmp=$(mktemp -d)
  if unzip -q -j -o "$CACHE/$zip" "*LICENSE.txt" -d "$tmp" 2>/dev/null && [[ -f "$tmp/LICENSE.txt" ]]; then mv "$tmp/LICENSE.txt" "$DEST/$lic"; fi
  rm -rf "$tmp"
done
echo "字体齐了：$DEST"
