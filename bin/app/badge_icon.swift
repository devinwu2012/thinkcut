// 开发版图标（2026-11-25 架构审查／用户同意“开发版程序和正式版分开”）：在正式图标右下角叠一个橙色“开发”角标，程序坞里一眼分得清。
// 用法（bin/build_app.sh 构建开发版时调用）：badge_icon <原图标.icns> <输出.iconset 文件夹>，之后 iconutil -c icns 合成。只用系统自带的 AppKit。
import AppKit

let args = CommandLine.arguments
guard args.count == 3, let src = NSImage(contentsOfFile: args[1]) else {
    FileHandle.standardError.write("用法：badge_icon <原图标.icns> <输出.iconset>\n".data(using: .utf8)!); exit(2)
}
let out = URL(fileURLWithPath: args[2])
try? FileManager.default.createDirectory(at: out, withIntermediateDirectories: true)

for (base, scale) in [(16, 1), (16, 2), (32, 1), (32, 2), (128, 1), (128, 2), (256, 1), (256, 2), (512, 1), (512, 2)] {
    let px = base * scale
    guard let rep = NSBitmapImageRep(bitmapDataPlanes: nil, pixelsWide: px, pixelsHigh: px, bitsPerSample: 8, samplesPerPixel: 4,
                                     hasAlpha: true, isPlanar: false, colorSpaceName: .deviceRGB, bytesPerRow: 0, bitsPerPixel: 0) else { exit(1) }
    rep.size = NSSize(width: px, height: px)
    NSGraphicsContext.saveGraphicsState()
    NSGraphicsContext.current = NSGraphicsContext(bitmapImageRep: rep)
    let s = CGFloat(px)
    src.draw(in: NSRect(x: 0, y: 0, width: s, height: s))
    // 角标：右下角橙色圆角块（苹果系统橙 #FF9500），白色粗体“开发”；小尺寸也看得出是个橙块
    let w = s * 0.56, h = s * 0.26, r = NSRect(x: s - w - s * 0.03, y: s * 0.04, width: w, height: h)
    let path = NSBezierPath(roundedRect: r, xRadius: h * 0.32, yRadius: h * 0.32)
    NSColor(srgbRed: 1, green: 0.584, blue: 0, alpha: 1).setFill(); path.fill()
    NSColor.white.withAlphaComponent(0.9).setStroke(); path.lineWidth = max(1, s * 0.012); path.stroke()
    if px >= 32 {
        let font = NSFont.systemFont(ofSize: h * 0.62, weight: .heavy)
        let text = NSAttributedString(string: "开发", attributes: [.font: font, .foregroundColor: NSColor.white])
        let ts = text.size()
        text.draw(at: NSPoint(x: r.midX - ts.width / 2, y: r.midY - ts.height / 2))
    }
    NSGraphicsContext.restoreGraphicsState()
    let name = scale == 1 ? "icon_\(base)x\(base).png" : "icon_\(base)x\(base)@2x.png"
    guard let png = rep.representation(using: .png, properties: [:]) else { exit(1) }
    try png.write(to: out.appendingPathComponent(name))
}
