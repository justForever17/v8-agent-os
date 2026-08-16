import AppKit
import ApplicationServices
import CoreGraphics
import Foundation

enum HelperError: Error {
    case message(String)
}

func readPayload() -> [String: Any] {
    let data = FileHandle.standardInput.readDataToEndOfFile()
    guard !data.isEmpty else { return [:] }
    guard let object = try? JSONSerialization.jsonObject(with: data) else { return [:] }
    return object as? [String: Any] ?? [:]
}

func emit(_ payload: Any) {
    let data = try! JSONSerialization.data(withJSONObject: payload, options: [.prettyPrinted])
    FileHandle.standardOutput.write(data)
}

func stringValue(_ value: Any?) -> String {
    return (value as? String)?.trimmingCharacters(in: .whitespacesAndNewlines) ?? ""
}

func intValue(_ value: Any?) -> Int? {
    if let raw = value as? Int { return raw }
    if let raw = value as? NSNumber { return raw.intValue }
    if let raw = value as? String, let parsed = Int(raw.trimmingCharacters(in: .whitespacesAndNewlines)) { return parsed }
    return nil
}

func boolValue(_ value: Any?) -> Bool {
    if let raw = value as? Bool { return raw }
    if let raw = value as? NSNumber { return raw.boolValue }
    if let raw = value as? String { return ["1", "true", "yes", "on"].contains(raw.lowercased()) }
    return false
}

func pointValue(_ value: Any?) -> CGPoint? {
    guard let array = value as? [Any], array.count == 2,
          let x = intValue(array[0]), let y = intValue(array[1]) else {
        return nil
    }
    return CGPoint(x: x, y: y)
}

func boundsArray(from rect: CGRect) -> [Int] {
    return [
        Int(rect.origin.x.rounded()),
        Int(rect.origin.y.rounded()),
        Int((rect.origin.x + rect.size.width).rounded()),
        Int((rect.origin.y + rect.size.height).rounded()),
    ]
}

func axString(_ element: AXUIElement, _ attribute: String) -> String {
    var value: CFTypeRef?
    let result = AXUIElementCopyAttributeValue(element, attribute as CFString, &value)
    guard result == .success else { return "" }
    if let stringValue = value as? String {
        return stringValue.trimmingCharacters(in: .whitespacesAndNewlines)
    }
    return ""
}

func axCGPoint(_ element: AXUIElement, _ attribute: String) -> CGPoint? {
    var value: CFTypeRef?
    let result = AXUIElementCopyAttributeValue(element, attribute as CFString, &value)
    guard result == .success, let axValue = value, CFGetTypeID(axValue) == AXValueGetTypeID() else {
        return nil
    }
    let typed = axValue as! AXValue
    if AXValueGetType(typed) == .cgPoint {
        var point = CGPoint.zero
        if AXValueGetValue(typed, .cgPoint, &point) {
            return point
        }
    }
    return nil
}

func axCGSize(_ element: AXUIElement, _ attribute: String) -> CGSize? {
    var value: CFTypeRef?
    let result = AXUIElementCopyAttributeValue(element, attribute as CFString, &value)
    guard result == .success, let axValue = value, CFGetTypeID(axValue) == AXValueGetTypeID() else {
        return nil
    }
    let typed = axValue as! AXValue
    if AXValueGetType(typed) == .cgSize {
        var size = CGSize.zero
        if AXValueGetValue(typed, .cgSize, &size) {
            return size
        }
    }
    return nil
}

func axBounds(_ element: AXUIElement) -> [Int]? {
    guard let position = axCGPoint(element, kAXPositionAttribute),
          let size = axCGSize(element, kAXSizeAttribute) else {
        return nil
    }
    return boundsArray(from: CGRect(origin: position, size: size))
}

func axActions(_ element: AXUIElement) -> [String] {
    var value: CFArray?
    let result = AXUIElementCopyActionNames(element, &value)
    guard result == .success, let actions = value else {
        return []
    }
    return (actions as NSArray).compactMap { $0 as? String }
}

func windowList() -> [[String: Any]] {
    guard let rawList = CGWindowListCopyWindowInfo([.optionOnScreenOnly, .excludeDesktopElements], kCGNullWindowID) as? [[String: Any]] else {
        return []
    }
    return rawList.compactMap { raw in
        let layer = (raw[kCGWindowLayer as String] as? NSNumber)?.intValue ?? 0
        let alpha = (raw[kCGWindowAlpha as String] as? NSNumber)?.doubleValue ?? 1.0
        if layer != 0 || alpha <= 0.01 { return nil }
        let title = stringValue(raw[kCGWindowName as String])
        let ownerName = stringValue(raw[kCGWindowOwnerName as String])
        if title.isEmpty && ownerName.isEmpty { return nil }
        let number = (raw[kCGWindowNumber as String] as? NSNumber)?.intValue ?? 0
        let pid = (raw[kCGWindowOwnerPID as String] as? NSNumber)?.intValue ?? 0
        let boundsDict = raw[kCGWindowBounds as String] as? [String: Any] ?? [:]
        let x = intValue(boundsDict["X"]) ?? 0
        let y = intValue(boundsDict["Y"]) ?? 0
        let width = intValue(boundsDict["Width"]) ?? 0
        let height = intValue(boundsDict["Height"]) ?? 0
        let running = NSRunningApplication(processIdentifier: pid_t(pid))
        return [
            "title": title,
            "handle": number,
            "processId": pid,
            "processName": ownerName.isEmpty ? stringValue(running?.localizedName) : ownerName,
            "bundleIdentifier": stringValue(running?.bundleIdentifier),
            "className": "AXWindow",
            "controlType": "Window",
            "bounds": [x, y, x + width, y + height],
            "isVisible": true,
            "isEnabled": true,
            "ownerName": ownerName,
        ]
    }
}

func matchedWindow(payload: [String: Any]) -> [String: Any]? {
    let handle = intValue(payload["window_handle"]) ?? intValue(payload["handle"])
    let title = stringValue(payload["window_title"]).lowercased()
    let processName = stringValue(payload["process_name"]).lowercased()
    let processId = intValue(payload["process_id"]) ?? intValue(payload["processId"])
    let windows = windowList()
    if let handle = handle, let matched = windows.first(where: { intValue($0["handle"]) == handle }) {
        return matched
    }
    let ranked = windows.map { window -> (Int, [String: Any]) in
        var score = 0
        let windowTitle = stringValue(window["title"]).lowercased()
        let windowProcessName = stringValue(window["processName"]).lowercased()
        let windowPid = intValue(window["processId"])
        if let processId = processId, processId == windowPid { score += 80 }
        if !processName.isEmpty && processName == windowProcessName { score += 40 }
        if !title.isEmpty {
            if windowTitle == title { score += 64 }
            else if windowTitle.contains(title) || title.contains(windowTitle) { score += 36 }
        }
        if boolValue(window["isVisible"]) { score += 6 }
        return (score, window)
    }.sorted { $0.0 > $1.0 }
    guard let best = ranked.first, best.0 > 0 else { return nil }
    return best.1
}

func foregroundWindow() -> [String: Any]? {
    guard let app = NSWorkspace.shared.frontmostApplication else { return nil }
    let pid = Int(app.processIdentifier)
    if let matched = windowList().first(where: { intValue($0["processId"]) == pid }) {
        return matched
    }
    return [
        "title": "",
        "handle": 0,
        "processId": pid,
        "processName": stringValue(app.localizedName),
        "bundleIdentifier": stringValue(app.bundleIdentifier),
        "className": "AXWindow",
        "controlType": "Window",
        "bounds": [],
        "isVisible": true,
        "isEnabled": true,
        "ownerName": stringValue(app.localizedName),
    ]
}

func activateWindow(payload: [String: Any]) throws -> [String: Any] {
    guard let matched = matchedWindow(payload: payload) ?? foregroundWindow() else {
        throw HelperError.message("未找到可聚焦的 macOS 窗口。")
    }
    let pid = intValue(matched["processId"]) ?? 0
    guard pid > 0, let app = NSRunningApplication(processIdentifier: pid_t(pid)) else {
        throw HelperError.message("未找到目标窗口所属进程。")
    }
    _ = app.activate(options: [.activateAllWindows, .activateIgnoringOtherApps])
    usleep(180_000)
    return matchedWindow(payload: ["process_id": pid, "window_title": matched["title"] as Any]) ?? matched
}

func elementDictionary(_ element: AXUIElement, path: [String], index: Int, windowHandle: Int?) -> [String: Any] {
    let role = axString(element, kAXRoleAttribute)
    let title = axString(element, kAXTitleAttribute)
    let value = axString(element, kAXValueAttribute)
    let description = axString(element, kAXDescriptionAttribute)
    let identifier = axString(element, kAXIdentifierAttribute)
    let subrole = axString(element, kAXSubroleAttribute)
    let bounds = axBounds(element) ?? []
    let name = !title.isEmpty ? title : (!value.isEmpty ? value : description)
    return [
        "elementId": "\(windowHandle ?? 0):\(index):\(role):\(identifier):\(path.joined(separator: "/"))",
        "backend": "macos_axui",
        "role": role.isEmpty ? "unknown" : role,
        "name": name,
        "bounds": bounds,
        "actions": axActions(element),
        "confidence": identifier.isEmpty ? 0.9 : 0.98,
        "path": path,
        "automationId": identifier,
        "className": subrole,
        "windowHandle": windowHandle as Any,
        "metadata": [
            "description": description,
            "value": value,
            "subrole": subrole,
        ],
    ]
}

func traverseAX(_ element: AXUIElement, depth: Int, maxDepth: Int, limit: Int, windowHandle: Int?, path: [String], output: inout [[String: Any]]) {
    if output.count >= limit || depth > maxDepth { return }
    if depth > 0 {
        output.append(elementDictionary(element, path: path, index: output.count, windowHandle: windowHandle))
    }
    var childrenValue: CFTypeRef?
    let childResult = AXUIElementCopyAttributeValue(element, kAXChildrenAttribute as CFString, &childrenValue)
    guard childResult == .success, let children = childrenValue as? [AXUIElement], !children.isEmpty else {
        return
    }
    for (index, child) in children.enumerated() {
        if output.count >= limit { break }
        let childRole = axString(child, kAXRoleAttribute)
        let childTitle = axString(child, kAXTitleAttribute)
        let label = !childTitle.isEmpty ? childTitle : (!childRole.isEmpty ? childRole : "node\(index)")
        traverseAX(child, depth: depth + 1, maxDepth: maxDepth, limit: limit, windowHandle: windowHandle, path: path + [label], output: &output)
    }
}

func axSnapshot(payload: [String: Any]) -> [String: Any] {
    let accessibilityGranted = AXIsProcessTrusted()
    let matched = matchedWindow(payload: payload) ?? foregroundWindow()
    guard accessibilityGranted else {
        return ["available": false, "reason": "Accessibility 权限未授予。", "window": matched as Any, "elements": []]
    }
    let depthLimit = max(1, intValue(payload["depth_limit"]) ?? intValue(payload["depthLimit"]) ?? 4)
    let elementLimit = max(1, intValue(payload["element_limit"]) ?? intValue(payload["elementLimit"]) ?? 80)
    let pid = intValue((matched ?? [:])["processId"]) ?? Int(NSWorkspace.shared.frontmostApplication?.processIdentifier ?? 0)
    if pid <= 0 {
        return ["available": false, "reason": "未能解析前台应用 PID。", "window": matched as Any, "elements": []]
    }
    var elements: [[String: Any]] = []
    let appElement = AXUIElementCreateApplication(pid_t(pid))
    traverseAX(appElement, depth: 0, maxDepth: depthLimit, limit: elementLimit, windowHandle: intValue((matched ?? [:])["handle"]), path: [stringValue((matched ?? [:])["title"]).isEmpty ? "window" : stringValue((matched ?? [:])["title"])], output: &elements)
    return ["available": true, "window": matched as Any, "elements": elements]
}

func eventFlags(_ modifiers: [String]) -> CGEventFlags {
    var flags = CGEventFlags()
    for raw in modifiers {
        switch raw.lowercased() {
        case "command", "cmd", "meta":
            flags.insert(.maskCommand)
        case "control", "ctrl":
            flags.insert(.maskControl)
        case "shift":
            flags.insert(.maskShift)
        case "option", "alt":
            flags.insert(.maskAlternate)
        default:
            break
        }
    }
    return flags
}

func keyCode(for key: String) -> CGKeyCode? {
    let normalized = key.lowercased()
    let mapping: [String: CGKeyCode] = [
        "a": 0, "s": 1, "d": 2, "f": 3, "h": 4, "g": 5, "z": 6, "x": 7, "c": 8, "v": 9,
        "b": 11, "q": 12, "w": 13, "e": 14, "r": 15, "y": 16, "t": 17, "1": 18, "2": 19,
        "3": 20, "4": 21, "6": 22, "5": 23, "=": 24, "9": 25, "7": 26, "-": 27, "8": 28,
        "0": 29, "]": 30, "o": 31, "u": 32, "[": 33, "i": 34, "p": 35, "l": 37, "j": 38,
        "'": 39, "k": 40, ";": 41, "\\": 42, ",": 43, "/": 44, "n": 45, "m": 46, ".": 47,
        "tab": 48, "space": 49, "return": 36, "enter": 36, "escape": 53, "esc": 53,
        "delete": 51, "backspace": 51, "forwarddelete": 117, "left": 123, "right": 124,
        "down": 125, "up": 126, "pageup": 116, "pagedown": 121, "home": 115, "end": 119
    ]
    return mapping[normalized]
}

func postMouseEvent(point: CGPoint, type: CGEventType, button: CGMouseButton = .left) throws {
    guard let event = CGEvent(mouseEventSource: nil, mouseType: type, mouseCursorPosition: point, mouseButton: button) else {
        throw HelperError.message("无法创建鼠标事件。")
    }
    event.post(tap: .cghidEventTap)
}

func clickPoint(payload: [String: Any], button: CGMouseButton, rightClick: Bool = false) throws -> [String: Any] {
    guard let point = pointValue(payload["point"]) else {
        throw HelperError.message("缺少 point。")
    }
    try postMouseEvent(point: point, type: .mouseMoved, button: button)
    usleep(40_000)
    try postMouseEvent(point: point, type: rightClick ? .rightMouseDown : .leftMouseDown, button: button)
    usleep(25_000)
    try postMouseEvent(point: point, type: rightClick ? .rightMouseUp : .leftMouseUp, button: button)
    return [
        "clickedPoint": [Int(point.x.rounded()), Int(point.y.rounded())],
        "role": "CoordinatePoint",
        "metadata": ["inputBackend": "cg_event", "route": "coordinate_fallback"],
    ]
}

func hoverPoint(payload: [String: Any]) throws -> [String: Any] {
    guard let point = pointValue(payload["point"]) else {
        throw HelperError.message("缺少 point。")
    }
    try postMouseEvent(point: point, type: .mouseMoved)
    return [
        "clickedPoint": [Int(point.x.rounded()), Int(point.y.rounded())],
        "role": "CoordinatePoint",
        "metadata": ["inputBackend": "cg_event", "route": "coordinate_fallback"],
    ]
}

func dragBetweenPoints(payload: [String: Any]) throws -> [String: Any] {
    guard let start = pointValue(payload["start_point"]),
          let end = pointValue(payload["end_point"]) else {
        throw HelperError.message("缺少 drag 起终点。")
    }
    let steps = max(4, intValue(payload["steps"]) ?? 12)
    try postMouseEvent(point: start, type: .mouseMoved)
    usleep(40_000)
    try postMouseEvent(point: start, type: .leftMouseDown)
    for index in 1...steps {
        let factor = CGFloat(index) / CGFloat(steps)
        let point = CGPoint(x: start.x + ((end.x - start.x) * factor), y: start.y + ((end.y - start.y) * factor))
        try postMouseEvent(point: point, type: .leftMouseDragged)
        usleep(14_000)
    }
    try postMouseEvent(point: end, type: .leftMouseUp)
    return [
        "startPoint": [Int(start.x.rounded()), Int(start.y.rounded())],
        "endPoint": [Int(end.x.rounded()), Int(end.y.rounded())],
        "metadata": ["inputBackend": "cg_event", "route": "coordinate_fallback"],
    ]
}

func scrollWheel(payload: [String: Any]) throws -> [String: Any] {
    let delta = intValue(payload["delta"]) ?? intValue(payload["amount"]) ?? 1
    guard let event = CGEvent(scrollWheelEvent2Source: nil, units: .line, wheelCount: 1, wheel1: Int32(delta), wheel2: 0, wheel3: 0) else {
        throw HelperError.message("无法创建滚轮事件。")
    }
    event.post(tap: .cghidEventTap)
    return ["delta": delta, "metadata": ["viewportStrategy": "scroll_wheel", "route": "coordinate_fallback"]]
}

func hotkey(payload: [String: Any]) throws -> [String: Any] {
    let key = stringValue(payload["key"])
    guard let keyCode = keyCode(for: key) else {
        throw HelperError.message("不支持的快捷键：\(key)")
    }
    let flags = eventFlags((payload["modifiers"] as? [String]) ?? [])
    guard let down = CGEvent(keyboardEventSource: nil, virtualKey: keyCode, keyDown: true),
          let up = CGEvent(keyboardEventSource: nil, virtualKey: keyCode, keyDown: false) else {
        throw HelperError.message("无法创建快捷键事件。")
    }
    down.flags = flags
    up.flags = flags
    down.post(tap: .cghidEventTap)
    usleep(20_000)
    up.post(tap: .cghidEventTap)
    return ["key": key, "modifiers": (payload["modifiers"] as? [String]) ?? [], "metadata": ["inputBackend": "cg_event", "route": "structured_accessibility"]]
}

func typeText(payload: [String: Any]) throws -> [String: Any] {
    let text = stringValue(payload["text"])
    if boolValue(payload["clear_first"]) {
        _ = try? hotkey(payload: ["key": "a", "modifiers": ["command"]])
        usleep(30_000)
        _ = try? hotkey(payload: ["key": "delete", "modifiers": []])
        usleep(30_000)
    }
    for scalar in text.unicodeScalars {
        guard let down = CGEvent(keyboardEventSource: nil, virtualKey: 0, keyDown: true),
              let up = CGEvent(keyboardEventSource: nil, virtualKey: 0, keyDown: false) else {
            throw HelperError.message("无法创建文本输入事件。")
        }
        var value = UniChar(scalar.value)
        down.keyboardSetUnicodeString(stringLength: 1, unicodeString: &value)
        up.keyboardSetUnicodeString(stringLength: 1, unicodeString: &value)
        down.post(tap: .cghidEventTap)
        up.post(tap: .cghidEventTap)
        usleep(10_000)
    }
    if boolValue(payload["press_enter"]) {
        _ = try? hotkey(payload: ["key": "enter", "modifiers": []])
    }
    return ["textLength": text.count, "metadata": ["inputBackend": "cg_event", "route": "structured_accessibility"]]
}

func probe() -> [String: Any] {
    let windows = windowList()
    let foreground = foregroundWindow()
    return [
        "platform": "macos",
        "backend": "axui",
        "accessibilityGranted": AXIsProcessTrusted(),
        "screenCaptureGranted": CGPreflightScreenCaptureAccess(),
        "swiftRuntimeAvailable": true,
        "frontmostWindow": foreground as Any,
        "windowCount": windows.count,
    ]
}

let command = CommandLine.arguments.count > 1 ? CommandLine.arguments[1] : "probe"
let payload = readPayload()

do {
    switch command {
    case "probe":
        emit(probe())
    case "list_windows":
        emit(["windows": windowList()])
    case "foreground_window":
        emit(["window": foregroundWindow() as Any])
    case "focus_window":
        emit(["window": try activateWindow(payload: payload)])
    case "ax_snapshot":
        emit(axSnapshot(payload: payload))
    case "click_point":
        emit(try clickPoint(payload: payload, button: .left))
    case "right_click_point":
        emit(try clickPoint(payload: payload, button: .right, rightClick: true))
    case "hover_point":
        emit(try hoverPoint(payload: payload))
    case "drag_between_points":
        emit(try dragBetweenPoints(payload: payload))
    case "scroll":
        emit(try scrollWheel(payload: payload))
    case "hotkey":
        emit(try hotkey(payload: payload))
    case "type_text":
        emit(try typeText(payload: payload))
    default:
        throw HelperError.message("不支持的 helper 命令：\(command)")
    }
} catch HelperError.message(let message) {
    emit(["error": message])
    exit(1)
} catch {
    emit(["error": "\(error)"])
    exit(1)
}
