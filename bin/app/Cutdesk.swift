// Cutdesk（ThinkCut · 思刻）Mac 程序外壳（2026-11-24 用户定：本地 Mac 程序，外壳套住现有网页界面，不内嵌模型、不碰账号）。
// 第二轮（2026-11-24 用户定：“双击直接就进去了……然后 ⌘W 直接退出来”）：程序里永远只有一个窗口，一次只开一条片子（达芬奇一次也只开一个工程）。
//   首页（thinkcut_edit.py home，本机 8760 端口）和片子的审片台都在这一个窗口里来回走；⌘W 在片子里＝回首页，在首页＝关窗口（不退出，⌘0 或点程序坞图标叫回）；⌘Q 才退出。
// 起不起首页、退出停不停，交给 bin/cutdesk_app.py 判断（离线测试在 bin/test_cutdesk_app.py）。
// 构建：bash bin/build_app.sh。程序文件夹位置：环境变量 CUTDESK_PROGRAM ＞ Info.plist 的 CutdeskProgram（构建时写进去）＞ 从 .app 往上找。
// 打开参数（可选）：open ThinkCut.app --args --port 8761 --root /某个/放片子的文件夹
//
// 和网页的约定（首页、审片台照这个写；细节见共享笔记 C2外壳_交接.md）：
//   · 首页双击卡片：location.href = 审片台网址?home=…#栏目（或首页的 /open?id=…，起好审片台再 302 跳过去），就在这个窗口里换页。
//     页面万一还调 window.open 开本机地址，也在这个窗口里载入，不开第二个窗口；外链交给系统浏览器。
//   · 页面里有 window.cutdeskApp（{version, titlebarHeight: 52, trafficLightsWidth: 92, showHome(), newFilm()}），<html> 带 class="cutdesk-app"：
//     页面自己画 52 高的工具栏，左边让出 92 留给红黄绿三颗灯；工具栏空白处按住能拖窗口、双击缩放（按钮、输入框、带 data-no-drag 的不算）。
//   · 菜单“新建片子 ⌘N”：先回首页，再在首页上发 cutdesk:new-film 事件（cancelable）；首页接了就 preventDefault()，没接外壳点页面上的“新建片子”。
//   · “全部片子 ⌘0”、cutdeskApp.showHome()：回首页（窗口关了就先拿出来）。
import AppKit
import WebKit

let python = "/usr/bin/python3"
let appName = (Bundle.main.object(forInfoDictionaryKey: "CFBundleName") as? String) ?? "ThinkCut"   // 正式版“ThinkCut”，开发版“ThinkCut 开发版”（build_app.sh 写进 Info.plist）
let defaultPort = (Bundle.main.object(forInfoDictionaryKey: "CutdeskPort") as? Int) ?? 8760          // 首页端口：正式版 8760，开发版 8762（两个能同时开、不串）
let checkDir = ProcessInfo.processInfo.environment["CUTDESK_CHECK"]     // 只给验收用（见文件末尾 Check）；平时不设
let version = (Bundle.main.object(forInfoDictionaryKey: "CFBundleShortVersionString") as? String) ?? "dev"
let toolbarHeight: CGFloat = 52

struct Launch: Decodable { let ok: Bool; let url: String; let started: Bool; let pid: Int?; let error: String; let log: String }

func findProgram() -> String? {
    let fm = FileManager.default
    func ok(_ p: String) -> Bool { fm.fileExists(atPath: p + "/bin/cutdesk_app.py") }
    if let p = ProcessInfo.processInfo.environment["CUTDESK_PROGRAM"], ok(p) { return p }
    if let p = Bundle.main.object(forInfoDictionaryKey: "CutdeskProgram") as? String, ok(p) { return p }
    var u = Bundle.main.bundleURL
    for _ in 0..<6 { u = u.deletingLastPathComponent(); if ok(u.path) { return u.path } }
    return nil
}

func arg(_ name: String) -> String? {
    let a = CommandLine.arguments
    if let i = a.firstIndex(of: name), i + 1 < a.count { return a[i + 1] }
    return nil
}

/// 本机地址留在窗口里，别的（外链）交给系统浏览器。
func isLocal(_ url: URL) -> Bool {
    guard let s = url.scheme?.lowercased() else { return true }
    if ["about", "data", "blob"].contains(s) { return true }
    guard s == "http" || s == "https" else { return false }
    return ["127.0.0.1", "localhost", "::1", "[::1]"].contains((url.host ?? "").lowercased())
}

/// 这个网址是不是首页本身（首页端口上、不是 /open 这种“起好审片台再跳过去”的中转）
func isHomePage(_ url: URL?, port: Int) -> Bool {
    guard let u = url, u.scheme?.hasPrefix("http") == true, u.port == port else { return false }
    return u.path != "/open"
}

func runPython(_ args: [String]) -> (Int32, String) {
    let p = Process(); p.executableURL = URL(fileURLWithPath: python); p.arguments = args
    let pipe = Pipe(); p.standardOutput = pipe; p.standardError = pipe; p.standardInput = FileHandle.nullDevice
    do { try p.run() } catch { return (-1, "运行不了 \(python)：\(error.localizedDescription)") }
    let data = pipe.fileHandleForReading.readDataToEndOfFile(); p.waitUntilExit()
    return (p.terminationStatus, String(decoding: data, as: UTF8.self))
}

func jsString(_ s: String) -> String {
    String(decoding: (try? JSONSerialization.data(withJSONObject: s, options: .fragmentsAllowed)) ?? Data("\"\"".utf8), as: UTF8.self)
}

func esc(_ s: String) -> String {
    s.replacingOccurrences(of: "&", with: "&amp;").replacingOccurrences(of: "<", with: "&lt;").replacingOccurrences(of: ">", with: "&gt;")
}

/// 外壳自己的提示页（正在打开、出错）：颜色照第二轮三块共用的那套（共享笔记 C2_配色和约定.md），跟系统深浅色走
func notice(_ title: String, _ body: String) -> String {
    """
    <!doctype html><meta charset="utf-8"><title>ThinkCut</title>
    <style>:root{--bg:#ffffff;--text:#1d1d1f;--text-2:#3a3a3c;--muted:#6e6e73;--chip:#f0f0f2;color-scheme:light dark}
    @media(prefers-color-scheme:dark){:root{--bg:#1e1e1e;--text:#f5f5f7;--text-2:#d1d1d6;--muted:#98989d;--chip:#3a3a3c}}
    body{font:14px -apple-system,BlinkMacSystemFont,"SF Pro Text","PingFang SC",sans-serif;margin:0;height:100vh;display:flex;align-items:center;justify-content:center;background:var(--bg);color:var(--text-2);-webkit-user-select:none;cursor:default}
    div{max-width:560px;padding:24px;line-height:1.7} h2{font-size:17px;font-weight:600;color:var(--text);margin:0 0 8px} p{margin:6px 0}
    code{font:12px ui-monospace,Menlo,monospace;word-break:break-all;background:var(--chip);border-radius:6px;padding:1px 6px;-webkit-user-select:text}</style>
    <div><h2>\(esc(title))</h2>\(body)</div>
    """
}

// 每个页面一开始就有的：告诉页面“在 ThinkCut 程序里”，以及工具栏空白处拖窗口、双击缩放（WKWebView 盖住了标题栏，系统不会自己拖）
let pageScript = """
(()=>{if(window.cutdeskApp)return;
const post=m=>{try{webkit.messageHandlers.cutdesk.postMessage(m)}catch(e){}},H=\(Int(toolbarHeight));
window.cutdeskApp={version:\(jsString(version)),titlebarHeight:H,trafficLightsWidth:92,showHome:()=>post({action:'home'}),newFilm:()=>post({action:'newFilm'})};
const mark=()=>document.documentElement&&document.documentElement.classList.add('cutdesk-app');mark();document.addEventListener('DOMContentLoaded',mark);
const NO='a,button,input,select,textarea,summary,label,video,audio,iframe,canvas,[contenteditable],[role=button],[role=tab],[role=menuitem],[role=slider],[role=textbox],[data-no-drag]';
const zone=e=>e.button===0&&e.clientY<H&&e.target instanceof Element&&!e.target.closest(NO);
addEventListener('mousedown',e=>{if(e.detail===1&&!e.defaultPrevented&&zone(e))post({action:'drag'})});
addEventListener('dblclick',e=>{if(zone(e))post({action:'zoom'})});})();
"""

// 菜单“新建片子”：先发事件给首页；首页没接就点页面上写着“新建片子”的按钮（首页还没改的时候也能用）
let newFilmJS = """
(()=>{const e=new CustomEvent('cutdesk:new-film',{cancelable:true});dispatchEvent(e);if(e.defaultPrevented)return 'page';
const p=document.getElementById('newpop');if(p){p.open=true;return 'fallback'}
const b=[...document.querySelectorAll('button,summary,a')].find(x=>(x.textContent.includes('新建项目')||x.textContent.includes('新建片子')));if(b){b.click();return 'button'}return 'none'})()
"""

final class CutWeb: WKWebView {
    var lastDown: NSEvent?          // 拖窗口要用按下那一下
    override func mouseDown(with e: NSEvent) { lastDown = e; super.mouseDown(with: e) }
}

final class Win: NSWindow {
    var web: CutWeb!
    var homeURL: URL?               // 首页网址（起好首页才有）
    var failedURL: URL?             // 上一次没连上的网址（⌘R 重试它）
}

final class Bridge: NSObject, WKScriptMessageHandler {
    weak var app: App?
    func userContentController(_ u: WKUserContentController, didReceive m: WKScriptMessage) {
        guard let app = app, let o = m.body as? [String: Any], let action = o["action"] as? String else { return }
        let web = m.webView as? CutWeb; let win = web?.window
        switch action {
        case "drag":     // 消息是异步到的：鼠标已经松开（只是点了一下）就不拖，不然窗口会粘在鼠标上直到下一次点击
            if NSEvent.pressedMouseButtons & 1 != 0, let w = win, let e = web?.lastDown ?? NSApp.currentEvent { w.performDrag(with: e) }
        case "zoom":
            let act = UserDefaults.standard.string(forKey: "AppleActionOnDoubleClick") ?? "Maximize"
            if act == "Minimize" { win?.performMiniaturize(nil) } else if act != "None" { win?.performZoom(nil) }
        case "home": app.showHome(nil)
        case "newFilm": app.newFilm(nil)
        default: break
        }
    }
}

final class App: NSObject, NSApplicationDelegate, WKNavigationDelegate, WKUIDelegate, NSWindowDelegate {
    var home: Win!                  // 唯一的窗口
    let bridge = Bridge()
    var port = Int(arg("--port") ?? "") ?? defaultPort
    var root = arg("--root")
    var program: String?
    var ours: Int?          // 自己起的首页的 pid；别人起的是 nil，退出时不停
    var pendingNewFilm = false
    var titleWatch: NSKeyValueObservation?

    func applicationDidFinishLaunching(_ n: Notification) {
        bridge.app = self
        buildMenu()
        let cfg = WKWebViewConfiguration()
        cfg.mediaTypesRequiringUserActionForPlayback = []
        cfg.preferences.isElementFullscreenEnabled = true
        cfg.preferences.javaScriptCanOpenWindowsAutomatically = true      // 页面先问服务、再 window.open 时不被拦（外壳在本窗口里载入）
        cfg.userContentController.add(bridge, name: "cutdesk")
        cfg.userContentController.addUserScript(WKUserScript(source: pageScript, injectionTime: .atDocumentStart, forMainFrameOnly: true))
        if checkDir != nil { Check.install(cfg) }
        home = makeWindow(CutWeb(frame: .zero, configuration: cfg), size: NSSize(width: 1400, height: 900), min: NSSize(width: 960, height: 600))
        home.title = appName
        home.isExcludedFromWindowsMenu = true            // 窗口菜单里用“全部片子 ⌘0”叫它
        titleWatch = home.web.observe(\.title) { [weak self] w, _ in       // 窗口标题（窗口菜单、调度中心里看得到）跟着页面：首页“ThinkCut”，片子里是片名
            guard let self = self else { return }
            self.home.title = (w.title?.isEmpty == false ? w.title! : appName)
        }
        home.center()
        home.setFrameAutosaveName("CutdeskMain")       // 记住大小位置
        home.makeKeyAndOrderFront(nil)
        NSApp.activate(ignoringOtherApps: true)
        home.web.loadHTMLString(notice("正在打开 ThinkCut…", "<p>第一次打开要几秒钟。</p>"), baseURL: nil)
        connect()
        if let d = checkDir { Check.shared.start(d, self) }
    }

    /// 窗口外观：标题栏和工具栏融成一体（页面自己画 52 高的工具栏），红黄绿三颗灯留在左上，跟系统深浅色。
    func makeWindow(_ web: CutWeb, size: NSSize, min: NSSize) -> Win {
        let w = Win(contentRect: NSRect(origin: .zero, size: size),
                    styleMask: [.titled, .closable, .miniaturizable, .resizable, .fullSizeContentView], backing: .buffered, defer: false)
        w.isReleasedWhenClosed = false
        w.titlebarAppearsTransparent = true; w.titleVisibility = .hidden
        w.toolbar = NSToolbar(identifier: "cutdesk")       // 空工具栏：把标题栏撑到 52 高，三颗灯在里面上下居中
        w.toolbarStyle = .unified
        w.contentMinSize = min
        web.navigationDelegate = self; web.uiDelegate = self
        if #available(macOS 13.3, *) { web.isInspectable = true }
        web.underPageBackgroundColor = .windowBackgroundColor
        w.web = web; w.contentView = web; w.delegate = self
        return w
    }

    func connect() {
        guard let prog = findProgram() else {
            home.web.loadHTMLString(notice("找不到 ThinkCut 的程序文件夹", "<p>这个程序是从某个程序文件夹构建出来的，那个文件夹现在找不到了（可能挪了地方、或硬盘没接上）。</p><p>接上硬盘后重新打开；挪过地方就在新位置重新构建一次：<code>bash bin/build_app.sh</code></p>"), baseURL: nil)
            return
        }
        program = prog
        var a = [prog + "/bin/cutdesk_app.py", "start", "--port", String(port)]
        if let r = root { a += ["--root", r] }
        DispatchQueue.global().async {
            let (code, out) = runPython(a)
            let line = out.split(separator: "\n").last.map(String.init) ?? ""
            let got = try? JSONDecoder().decode(Launch.self, from: Data(line.utf8))
            DispatchQueue.main.async { self.loaded(got, code: code, raw: out) }
        }
    }

    func loaded(_ got: Launch?, code: Int32, raw: String) {
        let web = home.web!
        guard let g = got else {
            web.loadHTMLString(notice("ThinkCut 没打开", "<p>启动程序出错了：</p><p><code>\(esc(String(raw.suffix(800))))</code></p>"), baseURL: nil); return
        }
        if g.ok, let u = URL(string: g.url) {
            if g.started { ours = g.pid }
            home.homeURL = u
            web.load(URLRequest(url: u)); return
        }
        var body = "<p>\(esc(g.error))</p>"
        if !g.log.isEmpty { body += "<p>详细记录在：<code>\(esc(g.log))</code></p>" }
        body += "<p>处理好后，按 ⌘R 再试一次。</p>"
        web.loadHTMLString(notice("ThinkCut 没打开", body), baseURL: nil)
    }

    // —— 菜单命令 ——
    var onHome: Bool { isHomePage(home.web.url, port: port) }

    /// 回首页：窗口关了就先拿出来；已经在首页就不重新载入
    func goHome() {
        home.makeKeyAndOrderFront(nil)
        guard !onHome else { return }
        if let u = home.homeURL { home.web.load(URLRequest(url: u)) } else { connect() }
    }

    /// 现在显示的是外壳自己的提示页（正在打开、连不上），不是首页或审片台
    var onNotice: Bool { home.web.url == nil || home.web.url?.scheme == "about" }

    /// ⌘R：连不上的是首页（或首页还没起来过）＝重新起首页；连不上的是审片台＝让首页按端口找回是哪条片子、起好再跳过去（首页也不认识就回首页）。QA 1.7
    @objc func reload(_ s: Any?) {
        if onNotice {
            if let u = home.failedURL, !isHomePage(u, port: port), let p = u.port, let h = home.homeURL,
               let back = URL(string: "/reopen?port=\(p)", relativeTo: h) { home.web.load(URLRequest(url: back.absoluteURL)) }
            else { connect() }
        } else { home.web.reload() }
    }

    @objc func showHome(_ s: Any?) { goHome() }

    /// ⌘W：在片子里＝回首页（不关窗口）；已经在首页（或是外壳自己的提示页）＝照常关窗口，程序不退出
    @objc func closeOrHome(_ s: Any?) {
        guard let w = NSApp.keyWindow else { return }
        let homeDown = onNotice && (home.failedURL == nil || isHomePage(home.failedURL, port: port))     // 首页自己起不来／连不上：⌘W 直接关窗口，不再去载入连不上的首页（QA 1.7）
        if w === home, home.isVisible, !onHome, !homeDown, home.homeURL != nil { goHome() } else { w.performClose(s) }
    }

    @objc func newFilm(_ s: Any?) {
        if onHome { home.makeKeyAndOrderFront(nil); home.web.evaluateJavaScript(newFilmJS, completionHandler: nil) }
        else { pendingNewFilm = true; goHome() }                             // 首页载入好了再发（didFinish 里）
    }

    // 关掉窗口不退出（⌘0 或点程序坞图标叫回来）；⌘Q 才退出
    func applicationShouldTerminateAfterLastWindowClosed(_ s: NSApplication) -> Bool { false }

    func applicationShouldHandleReopen(_ s: NSApplication, hasVisibleWindows v: Bool) -> Bool {
        if !v { home.makeKeyAndOrderFront(nil) }          // 点程序坞图标：窗口关了就拿出来（停在关之前那一页）
        return true
    }

    /// 退出、关窗口前先让页面把没存的字发出去（页面的 window.cutdeskFlush，最多等 3 秒；WKWebView 关掉时不一定触发 pagehide，QA 1.3）
    func flush(_ then: @escaping () -> Void) {
        var done = false
        let finish = { if !done { done = true; then() } }
        DispatchQueue.main.asyncAfter(deadline: .now() + 3.5, execute: finish)
        home.web.callAsyncJavaScript("if (window.cutdeskFlush) await Promise.race([window.cutdeskFlush(), new Promise(r => setTimeout(r, 3000))]);",
                                     arguments: [:], in: nil, in: .page) { _ in DispatchQueue.main.async(execute: finish) }
    }

    var flushedQuit = false
    func applicationShouldTerminate(_ s: NSApplication) -> NSApplication.TerminateReply {
        if flushedQuit || home == nil || onNotice { return .terminateNow }
        flush { self.flushedQuit = true; NSApp.reply(toApplicationShouldTerminate: true) }
        return .terminateLater
    }

    var flushedClose = false
    func windowShouldClose(_ w: NSWindow) -> Bool {
        if flushedClose || w !== home || onNotice { flushedClose = false; return true }
        flush { self.flushedClose = true; w.performClose(nil) }
        return false
    }

    func applicationWillTerminate(_ n: Notification) {
        guard let pid = ours, let prog = program else { return }       // 别人起的首页不停；各片子的审片台都不停
        _ = runPython([prog + "/bin/cutdesk_app.py", "stop", "--port", String(port), "--pid", String(pid)])
    }

    // —— 链接：本机地址留在窗口里；外链用系统浏览器；拖文件没落在接收区时不让整页变成那个文件 ——
    func webView(_ w: WKWebView, decidePolicyFor a: WKNavigationAction, decisionHandler h: @escaping (WKNavigationActionPolicy) -> Void) {
        guard let u = a.request.url else { return h(.allow) }
        if u.isFileURL { return h(.cancel) }
        let main = a.targetFrame?.isMainFrame ?? true
        if checkDir != nil, main { NSLog("cutdesk nav %d %@", a.navigationType.rawValue, u.absoluteString) }   // 验收时记下每次换页
        if !isLocal(u) && (main || a.navigationType == .linkActivated) { NSWorkspace.shared.open(u); return h(.cancel) }
        h(.allow)
    }

    // 新窗口（window.open、target=_blank）：外链交给系统浏览器；本机地址就在这个窗口里载入（程序里只有一个窗口）
    func webView(_ w: WKWebView, createWebViewWith c: WKWebViewConfiguration, for a: WKNavigationAction, windowFeatures f: WKWindowFeatures) -> WKWebView? {
        guard let u = a.request.url, !u.isFileURL else { return nil }
        if !isLocal(u) { NSWorkspace.shared.open(u); return nil }
        home.makeKeyAndOrderFront(nil)
        home.web.load(URLRequest(url: u))
        return nil
    }

    func webView(_ w: WKWebView, didFinish n: WKNavigation!) {
        if w.url?.scheme?.hasPrefix("http") == true { home.failedURL = nil }
        if pendingNewFilm, onHome { pendingNewFilm = false; w.evaluateJavaScript(newFilmJS, completionHandler: nil) }
    }

    func webViewWebContentProcessDidTerminate(_ w: WKWebView) { w.reload() }

    func webView(_ w: WKWebView, didFailProvisionalNavigation n: WKNavigation!, withError e: Error) {
        if (e as NSError).code == NSURLErrorCancelled || (e as NSError).domain == "WebKitErrorDomain" { return }
        let bad = (e as NSError).userInfo[NSURLErrorFailingURLErrorKey] as? URL; home.failedURL = bad
        let film = !isHomePage(bad, port: port) && home.homeURL != nil
        let why = (e as NSError).code == NSURLErrorCannotConnectToHost ? (film ? "它的审片台没在运行（可能已经停了）。" : "首页没在运行。") : e.localizedDescription
        w.loadHTMLString(notice(film ? "无法连接本项目的审片台" : "无法连接 ThinkCut", "<p>\(esc(why))</p><p>按 ⌘R 重试\(film ? "，或按 ⌘W 返回全部项目" : "")。</p>"), baseURL: nil)
    }

    // —— 页面里的提示框、确认框、输入框、选文件 ——
    func alert(_ text: String, _ buttons: [String]) -> NSAlert {
        let al = NSAlert(); al.messageText = text; buttons.forEach { al.addButton(withTitle: $0) }; return al
    }
    func webView(_ w: WKWebView, runJavaScriptAlertPanelWithMessage m: String, initiatedByFrame f: WKFrameInfo, completionHandler h: @escaping () -> Void) {
        alert(m, ["好"]).runModal(); h()
    }
    func webView(_ w: WKWebView, runJavaScriptConfirmPanelWithMessage m: String, initiatedByFrame f: WKFrameInfo, completionHandler h: @escaping (Bool) -> Void) {
        h(alert(m, ["确定", "取消"]).runModal() == .alertFirstButtonReturn)
    }
    func webView(_ w: WKWebView, runJavaScriptTextInputPanelWithPrompt p: String, defaultText d: String?, initiatedByFrame f: WKFrameInfo, completionHandler h: @escaping (String?) -> Void) {
        let al = alert(p, ["确定", "取消"]); let tf = NSTextField(frame: NSRect(x: 0, y: 0, width: 320, height: 24))
        tf.stringValue = d ?? ""; al.accessoryView = tf; al.window.initialFirstResponder = tf
        h(al.runModal() == .alertFirstButtonReturn ? tf.stringValue : nil)
    }
    func webView(_ w: WKWebView, runOpenPanelWith p: WKOpenPanelParameters, initiatedByFrame f: WKFrameInfo, completionHandler h: @escaping ([URL]?) -> Void) {
        let op = NSOpenPanel(); op.allowsMultipleSelection = p.allowsMultipleSelection
        op.canChooseDirectories = p.allowsDirectories; op.canChooseFiles = true
        op.beginSheetModal(for: w.window ?? home) { h($0 == .OK ? op.urls : nil) }
    }

    func buildMenu() {
        let bar = NSMenu()
        func menu(_ title: String, _ items: [(String, Selector?, String)], mine: Set<String> = []) -> NSMenu {
            let m = NSMenu(title: title)
            for (t, s, k) in items {
                if t == "-" { m.addItem(.separator()); continue }
                let it = m.addItem(withTitle: t, action: s, keyEquivalent: k)
                if mine.contains(t) { it.target = self }
            }
            let it = NSMenuItem(); it.submenu = m; bar.addItem(it); return m
        }
        _ = menu(appName, [("关于 \(appName)", #selector(NSApplication.orderFrontStandardAboutPanel(_:)), ""), ("-", nil, ""),
                         ("隐藏 \(appName)", #selector(NSApplication.hide(_:)), "h"), ("退出 \(appName)", #selector(NSApplication.terminate(_:)), "q")])
        _ = menu("文件", [("新建项目", #selector(newFilm(_:)), "n"), ("-", nil, ""), ("关闭", #selector(closeOrHome(_:)), "w")], mine: ["新建项目", "关闭"])
        _ = menu("编辑", [("撤销", Selector(("undo:")), "z"), ("重做", Selector(("redo:")), "Z"), ("-", nil, ""),
                        ("剪切", #selector(NSText.cut(_:)), "x"), ("拷贝", #selector(NSText.copy(_:)), "c"),
                        ("粘贴", #selector(NSText.paste(_:)), "v"), ("全选", #selector(NSText.selectAll(_:)), "a")])
        _ = menu("显示", [("重新载入", #selector(reload(_:)), "r")], mine: ["重新载入"])
        let wm = menu("窗口", [("最小化", #selector(NSWindow.performMiniaturize(_:)), "m"), ("缩放", #selector(NSWindow.performZoom(_:)), ""), ("-", nil, ""),
                             ("全部项目", #selector(showHome(_:)), "0")], mine: ["全部项目"])
        NSApp.mainMenu = bar; NSApp.windowsMenu = wm
    }
}

// —— 验收通道：设了 CUTDESK_CHECK=<文件夹> 才开。往里放 cmd.json，程序执行后写 out.json。
//   {"snap": "图片路径"} 给窗口内容拍照；{"js": "表达式", "frame": "8765"} 在页面（或网址含这个字的内嵌框）里算 JS；
//   {"drop": [x, y], "files": ["路径"]} 在窗口内容的 (x, y)（左上角起）模拟从访达拖文件进来（走窗口真正的拖放入口）；{"frames": true} 列出内嵌框；
//   {"windows": true} 列出窗口；{"menus": true} 列出菜单栏里每个菜单的每一项（含 macOS 自己加进去的）；{"key": "0", "mods": ["cmd"], "front": false} 把按键交给菜单（"front": false＝不先把窗口拿到前面，测关了的窗口能不能叫回）；
//   {"click": [x, y], "count": 2} 在窗口 (x, y)（左上角起）模拟鼠标点一下（count 2＝双击）（走窗口真正的事件入口，标题栏区域也一样）；{"hit": [x, y]} 看那一点归哪个视图。
final class Check: NSObject, WKScriptMessageHandler, NSDraggingInfo {
    static let shared = Check()
    var frames: [String: WKFrameInfo] = [:]
    var dir = ""; weak var app: App?; var dragWin: Win?
    static func install(_ cfg: WKWebViewConfiguration) {
        cfg.userContentController.add(shared, name: "cutdeskCheck")
        cfg.userContentController.addUserScript(WKUserScript(source: "webkit.messageHandlers.cutdeskCheck.postMessage(location.href)", injectionTime: .atDocumentEnd, forMainFrameOnly: false))
    }
    func userContentController(_ u: WKUserContentController, didReceive m: WKScriptMessage) { frames[String(describing: m.body)] = m.frameInfo }
    func start(_ d: String, _ a: App) {
        dir = d; app = a
        Timer.scheduledTimer(withTimeInterval: 0.3, repeats: true) { _ in self.poll() }
    }
    func done(_ v: Any) {
        var obj: [String: Any] = ["result": v]
        if !JSONSerialization.isValidJSONObject(obj) { obj = ["result": String(describing: v)] }
        let data = (try? JSONSerialization.data(withJSONObject: obj)) ?? Data("{\"result\":\"(不能转成 JSON)\"}".utf8)
        try? data.write(to: URL(fileURLWithPath: dir + "/out.json.tmp"))
        try? FileManager.default.moveItem(atPath: dir + "/out.json.tmp", toPath: dir + "/out.json")
    }
    func target(_ c: [String: Any]) -> Win? { app?.home }
    func describe(_ w: Win) -> [String: Any] {
        let f = w.frame, b = w.standardWindowButton(.closeButton)
        let lights = b.map { $0.convert($0.bounds, to: nil) } ?? .zero
        return ["title": w.title, "url": w.web.url?.absoluteString ?? "", "onHome": app?.onHome ?? false,
                "visible": w.isVisible, "key": w.isKeyWindow, "order": NSApp.orderedWindows.firstIndex(of: w) ?? -1, "autosave": w.frameAutosaveName,
                "frame": [f.minX, f.minY, f.width, f.height], "content": [w.contentLayoutRect.height, w.web.frame.height],
                "closeButton": [lights.minX, f.height - lights.maxY, lights.width, lights.height],
                "inWindowMenu": !w.isExcludedFromWindowsMenu, "appearance": w.effectiveAppearance.name.rawValue]
    }
    func poll() {
        let p = dir + "/cmd.json"
        guard let app = app, let data = FileManager.default.contents(atPath: p) else { return }
        try? FileManager.default.removeItem(atPath: p); try? FileManager.default.removeItem(atPath: dir + "/out.json")
        guard let c = (try? JSONSerialization.jsonObject(with: data)) as? [String: Any] else { return done("cmd.json 不是 JSON") }
        if c["menus"] != nil {          // 菜单栏每个菜单里的每一项（含系统自己加的，查有没有英文）
            return done((NSApp.mainMenu?.items ?? []).map { ["menu": $0.submenu?.title ?? $0.title, "items": $0.submenu?.items.filter { !$0.isSeparatorItem && !$0.isHidden && !$0.isAlternate }.map { $0.title } ?? []] as [String: Any] })
        }
        if c["windows"] != nil {
            return done([app.home].map(describe) + [["windowCount": NSApp.windows.filter { $0 is Win }.count], ["windowsMenu": NSApp.windowsMenu?.items.map { $0.title } ?? [], "active": NSApp.isActive]])
        }
        guard let win = target(c) else { return done("没找到窗口 \(c["win"] ?? "")") }
        let web = win.web!
        if let path = c["snap"] as? String {
            web.takeSnapshot(with: nil) { img, err in
                guard let img = img, let t = img.tiffRepresentation, let png = NSBitmapImageRep(data: t)?.representation(using: .png, properties: [:]) else { return self.done("拍照失败 \(String(describing: err))") }
                try? png.write(to: URL(fileURLWithPath: path)); self.done(path)
            }
        } else if let js = c["js"] as? String {
            let f = (c["frame"] as? String).flatMap { k in frames.first { $0.key.contains(k) && $0.value.webView === web }?.value }
            if c["frame"] != nil && f == nil { return done("没找到内嵌框 \(c["frame"]!)") }
            web.evaluateJavaScript(js, in: f, in: .page) { r in
                switch r { case .success(let v): self.done(v); case .failure(let e): self.done("JS 出错：\(e)") }
            }
        } else if c["frames"] != nil {
            done(frames.filter { $0.value.webView === web }.map { $0.key })
        } else if let k = c["key"] as? String {
            NSApp.activate(ignoringOtherApps: true); if (c["front"] as? Bool) != false { win.makeKeyAndOrderFront(nil) }
            var m: NSEvent.ModifierFlags = []
            for x in (c["mods"] as? [String]) ?? ["cmd"] { m.insert(["cmd": .command, "shift": .shift, "opt": .option, "ctrl": .control][x] ?? []) }
            DispatchQueue.main.asyncAfter(deadline: .now() + 0.4) {                  // 等程序真的到前面、窗口真的成了当前窗口
                let before = (NSApp.keyWindow as? Win)?.title ?? ""
                let ev = NSEvent.keyEvent(with: .keyDown, location: .zero, modifierFlags: m, timestamp: ProcessInfo.processInfo.systemUptime, windowNumber: win.windowNumber,
                                          context: nil, characters: k, charactersIgnoringModifiers: k, isARepeat: false, keyCode: 0)!
                let handled = NSApp.mainMenu?.performKeyEquivalent(with: ev) ?? false
                DispatchQueue.main.asyncAfter(deadline: .now() + 0.5) { self.done(["handled": handled, "keyBefore": before, "key": (NSApp.keyWindow as? Win).map(self.describe) ?? [:]]) }
            }
        } else if let xy = c["click"] as? [Double] {
            NSApp.activate(ignoringOtherApps: true); win.makeKeyAndOrderFront(nil)
            let pt = NSPoint(x: xy[0], y: win.frame.height - xy[1])
            DispatchQueue.main.asyncAfter(deadline: .now() + 0.4) {                  // 等程序到前面，不然第一下只是把窗口叫醒
                for n in 1...((c["count"] as? Int) ?? 1) {                        // "count": 2＝双击
                    for t in [NSEvent.EventType.leftMouseDown, .leftMouseUp] {
                        let ev = NSEvent.mouseEvent(with: t, location: pt, modifierFlags: [], timestamp: ProcessInfo.processInfo.systemUptime, windowNumber: win.windowNumber,
                                                    context: nil, eventNumber: 0, clickCount: n, pressure: t == .leftMouseDown ? 1 : 0)!
                        win.sendEvent(ev)
                    }
                }
                self.done(["clicked": xy, "active": NSApp.isActive, "key": win.isKeyWindow])
            }
        } else if let xy = c["hit"] as? [Double], let frame = win.contentView?.superview {
            let v = frame.hitTest(frame.convert(NSPoint(x: xy[0], y: win.frame.height - xy[1]), from: nil))
            var chain: [String] = []; var x = v
            while let y = x, chain.count < 4 { chain.append(String(describing: type(of: y))); x = y.superview }
            done(chain)
        } else if let xy = c["drop"] as? [Double], let files = c["files"] as? [String] {
            dragWin = win; drop(NSPoint(x: xy[0], y: xy[1]), files.map { URL(fileURLWithPath: $0) as NSURL })
        } else { done("不认识的命令") }
    }
    // 模拟拖放
    var pb = NSPasteboard(name: NSPasteboard.Name("cutdesk-check"))
    var at = NSPoint.zero
    func drop(_ p: NSPoint, _ urls: [NSURL]) {
        let web = dragWin!.web!
        pb.clearContents(); pb.writeObjects(urls)
        at = web.convert(p, to: nil)
        var op = web.draggingEntered(self); var n = 0
        func step() {
            op = web.draggingUpdated(self); n += 1
            if op.isEmpty && n < 30 { DispatchQueue.main.asyncAfter(deadline: .now() + 0.1, execute: step); return }
            if op.isEmpty { web.draggingExited(self); return done("页面不接这个拖放（\(n) 次都是 none）") }
            let ok = web.prepareForDragOperation(self) && web.performDragOperation(self)
            web.concludeDragOperation(self); done(["accepted": ok, "operation": op.rawValue, "tries": n])
        }
        DispatchQueue.main.asyncAfter(deadline: .now() + 0.1, execute: step)
    }
    var draggingDestinationWindow: NSWindow? { dragWin }
    var draggingSourceOperationMask: NSDragOperation { [.copy, .generic, .link] }
    var draggingLocation: NSPoint { at }
    var draggedImageLocation: NSPoint { at }
    var draggedImage: NSImage? { nil }
    var draggingPasteboard: NSPasteboard { pb }
    var draggingSource: Any? { nil }
    var draggingSequenceNumber: Int { 4242 }
    func slideDraggedImage(to screenPoint: NSPoint) {}
    override func namesOfPromisedFilesDropped(atDestination dropDestination: URL) -> [String]? { nil }
    var draggingFormation: NSDraggingFormation { get { .default } set {} }
    var animatesToDestination: Bool { get { false } set {} }
    var numberOfValidItemsForDrop: Int { get { (pb.pasteboardItems ?? []).count } set {} }
    func enumerateDraggingItems(options enumOpts: NSDraggingItemEnumerationOptions = [], for view: NSView?, classes classArray: [AnyClass], searchOptions: [NSPasteboard.ReadingOptionKey: Any] = [:], using block: @escaping (NSDraggingItem, Int, UnsafeMutablePointer<ObjCBool>) -> Void) {}
    var springLoadingHighlight: NSSpringLoadingHighlight { .none }
    func resetSpringLoading() {}
}

// 构建自检：bash bin/build_app.sh 之后测试会跑 Cutdesk --selftest（不开窗口）
if CommandLine.arguments.contains("--selftest") {
    var bad: [String] = []
    for (s, want) in [("http://127.0.0.1:8765/x", true), ("http://localhost:8760/", true), ("about:blank", true),
                      ("https://www.example.com/", false), ("file:///tmp/a.mp4", false), ("mailto:a@b.c", false)] {
        if isLocal(URL(string: s)!) != want { bad.append(s) }
    }
    for (s, want) in [("http://127.0.0.1:8760/", true), ("http://127.0.0.1:8760/#列表", true), ("http://127.0.0.1:8760/open?id=a", false),
                      ("http://127.0.0.1:8801/?home=x", false), ("about:blank", false)] where isHomePage(URL(string: s), port: 8760) != want { bad.append(s) }
    let prog = findProgram()
    print(bad.isEmpty ? "链接判断、首页判断 ok" : "链接判断或首页判断错：\(bad)")
    print("程序文件夹 \(prog ?? "找不到")")
    exit(bad.isEmpty && prog != nil ? 0 : 1)
}

let app = NSApplication.shared
let delegate = App()
app.delegate = delegate
app.setActivationPolicy(.regular)
app.run()
