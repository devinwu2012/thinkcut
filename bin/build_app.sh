#!/bin/bash
# 构建 Mac 程序外壳 ThinkCut.app（2026-11-24 用户定名 ThinkCut · 思刻；原叫 Cutdesk，内部文件名、可执行文件名、签名名沿用 Cutdesk）：只用系统自带的 swiftc（Xcode 命令行工具），不签名、不改系统安全设置。
#   bash bin/build_app.sh                 产出到 程序文件夹/build/ThinkCut.app（build/ 不进存档）
#   bash bin/build_app.sh --out 某个文件夹   产出到那里（测试用）
#   bash bin/build_app.sh --install       构建后放进“应用程序”文件夹（/Applications，写不进去就放 ~/Applications）
# 程序会记住是从哪个程序文件夹构建的（Info.plist 的 CutdeskProgram），打开时去那里起首页；文件夹挪了地方就重新构建一次。
set -euo pipefail
PROG="$(cd "$(dirname "$0")/.." && pwd)"
OUT="$PROG/build"; INSTALL=0
while [ $# -gt 0 ]; do
  case "$1" in
    --out) OUT="$2"; shift 2 ;;
    --install) INSTALL=1; shift ;;
    *) echo "不认识的参数：$1"; exit 2 ;;
  esac
done
command -v swiftc >/dev/null || { echo "停止：没有 swiftc。先装 Xcode 命令行工具：xcode-select --install"; exit 1; }
VER="$(cat "$PROG/VERSION" 2>/dev/null || echo 0)"
APP="$OUT/ThinkCut.app"; TMP="$(mktemp -d)"; trap 'rm -rf "$TMP"' EXIT
mkdir -p "$OUT"; rm -rf "$APP"; mkdir -p "$APP/Contents/MacOS" "$APP/Contents/Resources"

swiftc -O -o "$APP/Contents/MacOS/Cutdesk" "$PROG/bin/app/Cutdesk.swift" -framework AppKit -framework WebKit
# 图标：用定稿的“一刀·两层纸”（2026-11-24 用户选定，矢量原件 bin/app/Cutdesk.svg，来源见 内部/ 交接）；没有定稿文件时才用代码画的临时图标
if [ -f "$PROG/bin/app/Cutdesk.icns" ]; then
  cp "$PROG/bin/app/Cutdesk.icns" "$APP/Contents/Resources/Cutdesk.icns"
else
  swiftc -O -o "$TMP/make_icon" "$PROG/bin/app/make_icon.swift" -framework AppKit
  "$TMP/make_icon" "$TMP/Cutdesk.iconset"
  iconutil -c icns "$TMP/Cutdesk.iconset" -o "$APP/Contents/Resources/Cutdesk.icns"
fi

esc() { printf '%s' "$1" | sed -e 's/&/\&amp;/g' -e 's/</\&lt;/g' -e 's/>/\&gt;/g'; }
cat > "$APP/Contents/Info.plist" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>CFBundleName</key><string>ThinkCut</string>
  <key>CFBundleDisplayName</key><string>ThinkCut</string>
  <key>CFBundleIdentifier</key><string>local.cutdesk.app</string>
  <key>CFBundleExecutable</key><string>Cutdesk</string>
  <key>CFBundleIconFile</key><string>Cutdesk</string>
  <key>CFBundlePackageType</key><string>APPL</string>
  <key>CFBundleShortVersionString</key><string>$(esc "$VER")</string>
  <key>CFBundleVersion</key><string>$(esc "$VER")</string>
  <key>LSMinimumSystemVersion</key><string>12.0</string>
  <key>NSHighResolutionCapable</key><true/>
  <key>NSAppTransportSecurity</key><dict><key>NSAllowsLocalNetworking</key><true/></dict>
  <key>CutdeskProgram</key><string>$(esc "$PROG")</string>
</dict></plist>
EOF
plutil -lint "$APP/Contents/Info.plist" >/dev/null

# 签名（2026-11-24 用户定）：临时签名下 macOS 按程序内容认它，每构建一次就当成新程序、重新问“网络宗卷”等权限。
# 钥匙串里有“Cutdesk 本机签名”（本机自建证书，只在这台 Mac 用）就用它签，重建多少次都算同一个程序；
# 没有就退回临时签名。以后对外发布换成苹果开发者签名：设 CUTDESK_SIGN="Developer ID Application: …"。
SIGN="${CUTDESK_SIGN:-Cutdesk 本机签名}"
SHA="$(security find-certificate -c "$SIGN" -Z 2>/dev/null | awk '/SHA-1/ {print $3; exit}')"   # 用指纹签：名字带中文时 codesign 会报 unknown exception
if [ -n "$SHA" ]; then
  codesign --force --deep --sign "$SHA" --identifier local.cutdesk.app "$APP"
  echo "已签名：${SIGN}"
else
  codesign --force --deep --sign - --identifier local.cutdesk.app "$APP"
  echo "钥匙串里没有“${SIGN}”，用临时签名（每次重建 macOS 会重新问权限）"
fi
echo "已构建：${APP}（程序文件夹 ${PROG}）"

if [ "$INSTALL" = 1 ]; then
  DEST=/Applications; [ -w "$DEST" ] || { DEST="$HOME/Applications"; mkdir -p "$DEST"; }
  rm -rf "$DEST/ThinkCut.app" "$DEST/Cutdesk.app"; cp -R "$APP" "$DEST/"   # 改名前装过的 Cutdesk.app 一起换掉
  echo "已放进 $DEST/ThinkCut.app。第一次打开：本机自建证书苹果不认，系统若说“无法验证开发者”，到 系统设置 → 隐私与安全性 点“仍要打开”；之后问一次“网络宗卷”点允许，重建也不会再问"
fi
