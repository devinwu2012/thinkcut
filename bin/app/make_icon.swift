// 画 Cutdesk 的图标（构建时用，bin/build_app.sh 调）：深色圆角方块上一个白色“口”字和一条红色剪辑线。参数：输出的 .iconset 文件夹。
import AppKit

let out = CommandLine.arguments[1]
try? FileManager.default.createDirectory(atPath: out, withIntermediateDirectories: true)
for (px, name) in [(16, "16x16"), (32, "16x16@2x"), (32, "32x32"), (64, "32x32@2x"), (128, "128x128"), (256, "128x128@2x"),
                   (256, "256x256"), (512, "256x256@2x"), (512, "512x512"), (1024, "512x512@2x")] {
    let s = CGFloat(px)
    let rep = NSBitmapImageRep(bitmapDataPlanes: nil, pixelsWide: px, pixelsHigh: px, bitsPerSample: 8, samplesPerPixel: 4,
                               hasAlpha: true, isPlanar: false, colorSpaceName: .deviceRGB, bytesPerRow: 0, bitsPerPixel: 0)!
    NSGraphicsContext.saveGraphicsState()
    NSGraphicsContext.current = NSGraphicsContext(bitmapImageRep: rep)
    let box = NSRect(x: s * 0.1, y: s * 0.1, width: s * 0.8, height: s * 0.8)
    NSColor(red: 0.13, green: 0.20, blue: 0.36, alpha: 1).setFill()
    NSBezierPath(roundedRect: box, xRadius: s * 0.18, yRadius: s * 0.18).fill()
    let font = NSFont.systemFont(ofSize: s * 0.42, weight: .bold)
    let t = NSAttributedString(string: "口", attributes: [.font: font, .foregroundColor: NSColor.white])
    let ts = t.size()
    t.draw(at: NSPoint(x: (s - ts.width) / 2, y: s * 0.52 - ts.height / 2))
    NSColor(red: 0.93, green: 0.30, blue: 0.22, alpha: 1).setFill()
    NSBezierPath(roundedRect: NSRect(x: s * 0.24, y: s * 0.22, width: s * 0.52, height: s * 0.06), xRadius: s * 0.03, yRadius: s * 0.03).fill()
    NSGraphicsContext.restoreGraphicsState()
    try! rep.representation(using: .png, properties: [:])!.write(to: URL(fileURLWithPath: "\(out)/icon_\(name).png"))
}
