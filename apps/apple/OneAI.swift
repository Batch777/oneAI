import SwiftUI
import WebKit

@main
struct OneAIApp: App {
    var body: some Scene {
        WindowGroup {
            Workspace().preferredColorScheme(.light)
            #if os(macOS)
            .frame(minWidth: 940, minHeight: 650)
            #endif
        }
        #if os(macOS)
        .defaultSize(width: 1160, height: 820)
        #endif
    }
}

struct Workspace: View {
    @AppStorage("serverURL") private var server = "https://47.82.117.21"
    @State private var settings = false
    @State private var address = ""
    @State private var error: String?
    @State private var reloadID = UUID()
    private var endpoint: URL {
        #if DEBUG
        if let value = ProcessInfo.processInfo.environment["ONEAI_TEST_URL"] ?? Bundle.main.object(forInfoDictionaryKey: "OneAITestURL") as? String, let url = URL(string: value) { return url }
        #endif
        return URL(string: server) ?? URL(string: "https://47.82.117.21")!
    }
    var body: some View {
        VStack(spacing: 0) {
            HStack {
                Image(systemName: "circle.inset.filled").foregroundStyle(Color(red: 0.23, green: 0.35, blue: 0.25))
                Text("oneAI").font(.headline)
                Spacer()
                Button { reloadID = UUID(); error = nil } label: { Image(systemName: "arrow.clockwise") }.accessibilityLabel("重新连接")
                Button { address = server; settings = true } label: { Image(systemName: "gearshape") }.accessibilityLabel("服务器设置")
            }.padding(12).background(Color(red: 0.96, green: 0.95, blue: 0.91))
            if let error {
                VStack(spacing: 18) {
                    Image(systemName: "wifi.slash").font(.largeTitle)
                    Text("暂时无法连接").font(.title2)
                    Text(error).multilineTextAlignment(.center).foregroundStyle(.secondary)
                    Button("重试") { self.error = nil; reloadID = UUID() }
                    Text("云端仍会继续工作，已保存的任务不会丢失。").font(.caption).foregroundStyle(.secondary)
                }.padding(30).frame(maxWidth: .infinity, maxHeight: .infinity)
            } else {
                WebWorkspace(url: endpoint, failure: $error).id(reloadID)
            }
        }
        .sheet(isPresented: $settings) {
            VStack(alignment: .leading, spacing: 20) {
                Text("连接你的云端").font(.title2.bold())
                Text("填写 oneAI 服务地址，在网页中使用一次性配对码登录。").foregroundStyle(.secondary)
                TextField("https://你的服务器", text: $address).textFieldStyle(.roundedBorder)
                HStack {
                    Button("取消") { settings = false }
                    Spacer()
                    Button("连接") {
                        server = address.trimmingCharacters(in: .whitespacesAndNewlines)
                        error = nil; reloadID = UUID(); settings = false
                    }.disabled(!validAddress(address))
                }
            }.padding(24)
            #if os(macOS)
            .frame(width: 420)
            #endif
        }
    }
    func validAddress(_ value: String) -> Bool {
        guard let url = URL(string: value.trimmingCharacters(in: .whitespacesAndNewlines)), url.host != nil, url.user == nil, url.password == nil, url.query == nil, url.fragment == nil, url.path.isEmpty || url.path == "/" else { return false }
        return url.scheme == "https"
    }
}

final class Navigation: NSObject, WKNavigationDelegate, WKUIDelegate {
    let origin: URL
    var failure: Binding<String?>
    init(_ url: URL, _ failure: Binding<String?>) { self.origin = url; self.failure = failure }
    func webView(_ webView: WKWebView, didFailProvisionalNavigation navigation: WKNavigation!, withError error: Error) { failed(error) }
    func webView(_ webView: WKWebView, didFail navigation: WKNavigation!, withError error: Error) { failed(error) }
    func failed(_ error: Error) {
        if (error as NSError).code != NSURLErrorCancelled { failure.wrappedValue = "请检查网络和服务器地址，然后重新连接。" }
    }
    func webView(_ webView: WKWebView, decidePolicyFor action: WKNavigationAction, decisionHandler: @escaping (WKNavigationActionPolicy) -> Void) {
        guard let url = action.request.url, url.scheme == origin.scheme, url.host == origin.host, url.port == origin.port else { decisionHandler(.cancel); return }
        decisionHandler(.allow)
    }
    func webView(_ webView: WKWebView, runJavaScriptConfirmPanelWithMessage message: String, initiatedByFrame frame: WKFrameInfo, completionHandler: @escaping (Bool) -> Void) {
        #if os(macOS)
        let alert = NSAlert(); alert.messageText = message; alert.addButton(withTitle: "确认"); alert.addButton(withTitle: "取消")
        completionHandler(alert.runModal() == .alertFirstButtonReturn)
        #else
        let alert = UIAlertController(title: "请确认", message: message, preferredStyle: .alert)
        alert.addAction(UIAlertAction(title: "取消", style: .cancel) { _ in completionHandler(false) })
        alert.addAction(UIAlertAction(title: "确认", style: .default) { _ in completionHandler(true) })
        guard let controller = webView.window?.rootViewController else { completionHandler(false); return }
        controller.present(alert, animated: true)
        #endif
    }
}

#if os(macOS)
struct WebWorkspace: NSViewRepresentable {
    let url: URL
    @Binding var failure: String?
    func makeCoordinator() -> Navigation { Navigation(url, $failure) }
    func makeNSView(context: Context) -> WKWebView { makeWeb(url, context.coordinator) }
    func updateNSView(_ view: WKWebView, context: Context) {}
}
#else
struct WebWorkspace: UIViewRepresentable {
    let url: URL
    @Binding var failure: String?
    func makeCoordinator() -> Navigation { Navigation(url, $failure) }
    func makeUIView(context: Context) -> WKWebView { makeWeb(url, context.coordinator) }
    func updateUIView(_ view: WKWebView, context: Context) {}
}
#endif

func makeWeb(_ url: URL, _ delegate: Navigation) -> WKWebView {
    let config = WKWebViewConfiguration()
    config.websiteDataStore = .default()
    let web = WKWebView(frame: .zero, configuration: config)
    web.navigationDelegate = delegate; web.uiDelegate = delegate
    #if DEBUG
    web.isInspectable = true
    #endif
    web.load(URLRequest(url: url))
    return web
}
