// 用苹果系统（AVFoundation）按显示效果取帧，存 16 位 PNG；speed_voice.py 拿它和 ffmpeg 直读的帧对比算 HDR 颜色曲线。
// 用法：swift apple_frames.swift <视频> <输出文件夹> <秒> [<秒> …]
// 每取一帧打印一行：请求秒<TAB>实际取到的秒<TAB>PNG 路径
import AVFoundation
import AppKit

let args = CommandLine.arguments
guard args.count >= 4 else { FileHandle.standardError.write("用法：swift apple_frames.swift <视频> <输出文件夹> <秒>…\n".data(using: .utf8)!); exit(2) }
let generator = AVAssetImageGenerator(asset: AVURLAsset(url: URL(fileURLWithPath: args[1])))
generator.appliesPreferredTrackTransform = true
generator.requestedTimeToleranceBefore = .zero
generator.requestedTimeToleranceAfter = .zero
for (index, text) in args[3...].enumerated() {
    guard let seconds = Double(text) else { exit(2) }
    var actual = CMTime.zero
    do {
        let image = try generator.copyCGImage(at: CMTime(seconds: seconds, preferredTimescale: 60000), actualTime: &actual)
        guard let png = NSBitmapImageRep(cgImage: image).representation(using: .png, properties: [:]) else { exit(1) }
        let path = args[2] + "/frame_\(index).png"
        try png.write(to: URL(fileURLWithPath: path))
        print("\(text)\t\(String(format: "%.6f", CMTimeGetSeconds(actual)))\t\(path)")
    } catch {
        FileHandle.standardError.write("取帧失败 \(text) 秒：\(error)\n".data(using: .utf8)!)
        exit(1)
    }
}
