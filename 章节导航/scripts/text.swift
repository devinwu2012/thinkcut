import AppKit
import Foundation

func fail(_ message: String) -> Never {
    FileHandle.standardError.write(Data((message + "\n").utf8)); exit(1)
}
guard CommandLine.arguments.count == 2 else { fail("需要 render-spec.json") }
let data = try Data(contentsOf: URL(fileURLWithPath: CommandLine.arguments[1]))
let spec = try JSONSerialization.jsonObject(with: data) as! [String: Any]
let width = spec["width"] as! Int, height = spec["height"] as! Int
let style = spec["style"] as! [String: Any]
func value(_ key: String) -> CGFloat { CGFloat((style[key] as! NSNumber).doubleValue) * CGFloat(width) }
func color(_ key: String, alpha: CGFloat = 1) -> NSColor {
    let hex = String((style[key] as! String).dropFirst())
    let n = UInt32(hex, radix: 16)!
    return NSColor(srgbRed: CGFloat((n >> 16) & 255)/255, green: CGFloat((n >> 8) & 255)/255, blue: CGFloat(n & 255)/255, alpha: alpha)
}
for row in spec["rows"] as! [[String: Any]] {
    let bitmap = NSBitmapImageRep(bitmapDataPlanes: nil, pixelsWide: width, pixelsHigh: height, bitsPerSample: 8, samplesPerPixel: 4, hasAlpha: true, isPlanar: false, colorSpaceName: .deviceRGB, bytesPerRow: 0, bitsPerPixel: 0)!
    let context = NSGraphicsContext(bitmapImageRep: bitmap)!
    NSGraphicsContext.saveGraphicsState(); NSGraphicsContext.current = context
    color("background", alpha: CGFloat((style["background_alpha"] as? NSNumber)?.doubleValue ?? 1)).setFill(); NSRect(x: 0, y: 0, width: width, height: height).fill()
    var previousBottom: CGFloat = 0
    let bar = row["bar"] as? [String: Any]
    if let bar = bar {      // 短标签样式：分段底线 + 每段下面的短标签，正在讲的那段白色加粗
        let by = CGFloat(bar["y"] as! Int), bh = CGFloat(bar["h"] as! Int)
        for seg in bar["segments"] as! [[String: Any]] {
            color((seg["current"] as! Bool) ? "track_current" : "track").setFill()
            NSRect(x: CGFloat(seg["x"] as! Int), y: CGFloat(height) - by - bh, width: CGFloat(seg["w"] as! Int), height: bh).fill()
        }
        for item in row["labels"] as! [[String: Any]] {
            let text = item["text"] as! String, on = item["current"] as! Bool
            if text.isEmpty { continue }
            guard let font = NSFont(name: style[on ? "font_title" : "font_question"] as! String, size: CGFloat(bar["font"] as! Int)) else { fail("找不到字体") }
            var attrs: [NSAttributedString.Key: Any] = [.font: font, .foregroundColor: color(on ? "foreground" : "label_dim")]
            let sh = bar["shadow"] as! [String: Any], shadowAlpha = (sh["alpha"] as! NSNumber).doubleValue
            if shadowAlpha > 0 {      // 半透明／透明底：字下面垫一层暗影，亮画面上也看得清
                let shadow = NSShadow()
                shadow.shadowColor = NSColor(white: 0, alpha: CGFloat(shadowAlpha))
                shadow.shadowBlurRadius = CGFloat((sh["blur"] as! NSNumber).doubleValue) * 2
                shadow.shadowOffset = NSSize(width: 0, height: -CGFloat((sh["dy"] as! NSNumber).doubleValue))
                attrs[.shadow] = shadow
            }
            let str = NSAttributedString(string: text, attributes: attrs)
            let size = str.size(), top = CGFloat(bar["label_top"] as! Int), line = CGFloat(bar["line"] as! Int)      // line 由 Python 按 validate 同一个系数算好
            let boxW = CGFloat(item["w"] as! Int), cx = CGFloat(item["cx"] as! Int)
            var squeeze: CGFloat = 1      // 放不下就横向压窄（字高不变）；压到下限还放不下才报错
            if size.width > boxW { squeeze = (boxW - CGFloat(bar["pad"] as! Int)) / size.width }
            if squeeze < CGFloat((bar["squeeze_min"] as! NSNumber).doubleValue) - 0.000001 { fail("短标签超宽（压窄到下限也放不下），请换短一点的词：\(text)") }
            if size.height > line || top + line > CGFloat(height) { fail("导航高度不足，短标签放不下") }
            NSGraphicsContext.saveGraphicsState()
            if squeeze < 1 {
                let t = NSAffineTransform(); t.translateX(by: cx, yBy: 0); t.scaleX(by: squeeze, yBy: 1); t.translateX(by: -cx, yBy: 0); t.concat()
            }
            str.draw(at: NSPoint(x: cx - size.width / 2, y: CGFloat(height) - top - size.height))
            NSGraphicsContext.restoreGraphicsState()
        }
    }
    for (field, fontKey, sizeKey, topKey) in bar != nil ? [] : [("title", "font_title", "title_ratio", "title_top_ratio"), ("question", "font_question", "question_ratio", "question_top_ratio")] {
        if (row[field] as? String ?? "").isEmpty { continue }
        guard let font = NSFont(name: style[fontKey] as! String, size: value(sizeKey)) else { fail("找不到字体：\(style[fontKey]!)") }
        let str = NSAttributedString(string: row[field] as! String, attributes: [.font: font, .foregroundColor: color("foreground")])
        let size = str.size(), top = value(topKey), margin = value("margin_ratio")
        if size.width > CGFloat(width) - margin * 2 { fail("标题超宽，请精简或调整布局：\(row[field]!)") }
        if top < previousBottom || top + size.height > CGFloat(height) - value("track_bottom_ratio") - value("track_height_ratio") - 8 { fail("文字碰撞或导航高度不足") }
        str.draw(at: NSPoint(x: margin, y: CGFloat(height) - top - size.height))
        previousBottom = top + size.height
    }
    context.flushGraphics(); NSGraphicsContext.restoreGraphicsState()
    try bitmap.representation(using: .png, properties: [:])!.write(to: URL(fileURLWithPath: row["png"] as! String), options: .withoutOverwriting)
}
