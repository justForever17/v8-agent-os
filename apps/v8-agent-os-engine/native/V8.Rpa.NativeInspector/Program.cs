using System.Diagnostics;
using System.Drawing;
using System.Drawing.Imaging;
using System.Net.Http;
using System.Runtime.InteropServices;
using System.Text;
using System.Text.Json;
using System.Text.Json.Serialization;
using System.Windows;
using System.Windows.Controls;
using System.Windows.Interop;
using System.Windows.Media;
using System.Windows.Threading;
using FlaUI.Core;
using FlaUI.Core.AutomationElements;
using FlaUI.Core.Definitions;
using FlaUI.UIA2;
using FlaUI.UIA3;
using Point = System.Drawing.Point;
using Rect = System.Windows.Rect;

namespace V8.Rpa.NativeInspector;

internal static class Program
{
    [STAThread]
    private static int Main(string[] args)
    {
        try
        {
            var request = InspectorRequest.Load(args);
            Jsonl.Emit("rpa_capture_assistant.heartbeat", new { ok = true, stage = "starting", backend = "windows_fla_ui_helper" });
            if (!OperatingSystem.IsWindows())
            {
                Jsonl.Emit("rpa_capture_assistant.error", new
                {
                    ok = false,
                    stage = "backend_not_available",
                    error = "V8.Rpa.NativeInspector currently implements the Windows helper. macOS/Linux helpers use the same protocol but are not bundled in this binary.",
                    backend = "windows_fla_ui_helper"
                });
                return 2;
            }

            NativeMethods.SetBestEffortDpiAwareness();
            using var inspector = new WindowsInspector(request);
            return inspector.Run();
        }
        catch (Exception ex)
        {
            Jsonl.Emit("rpa_capture_assistant.error", new
            {
                ok = false,
                stage = "fatal",
                error = ex.Message,
                detail = ex.ToString(),
                backend = "windows_fla_ui_helper"
            });
            return 1;
        }
    }
}

internal sealed class WindowsInspector : IDisposable
{
    private readonly InspectorRequest _request;
    private readonly UiSampler _sampler;
    private readonly InputHooks _hooks;
    private readonly InspectorOverlay _overlay;
    private readonly DispatcherTimer _hoverTimer;
    private readonly DispatcherTimer _heartbeatTimer;
    private readonly Stopwatch _startedAt = Stopwatch.StartNew();
    private AutomationElement? _hoverElement;
    private NativeWindowInfo _targetWindow;
    private bool _capturePosted;
    private bool _captureInFlight;
    private Point _lastPointer;
    private long _lastPointerChangedAtMs;
    private long _lastCrosshairAtMs;
    private long _lastHoverEmitAtMs;
    private string _lastHoverSignature = "";
    private string _lastHintSignature = "";
    private const long DeepHoverSampleDelayMs = 200;
    private const long CrosshairUpdateMinMs = 32;
    private const long HoverEmitMinMs = 360;

    public WindowsInspector(InspectorRequest request)
    {
        _request = request;
        _targetWindow = NativeWindowInfo.Resolve(request);
        if (!_targetWindow.IsReady)
        {
            Jsonl.Emit("rpa_capture_assistant.error", new
            {
                ok = false,
                stage = "target_not_ready",
                error = "Target window is not visible or could not be resolved.",
                backend = "windows_fla_ui_helper",
                targetWindow = request.TargetWindow,
                targetProcess = request.TargetProcess
            });
        }
        _sampler = new UiSampler(_targetWindow);
        _overlay = new InspectorOverlay();
        _hooks = new InputHooks();
        _lastPointer = NativeMethods.GetCursorPoint();
        _lastPointerChangedAtMs = _startedAt.ElapsedMilliseconds;
        _hooks.CaptureRequested += (_, point) => _overlay.Dispatcher.BeginInvoke(() => CaptureAt(point));
        _hooks.CancelRequested += (_, _) => _overlay.Dispatcher.BeginInvoke(Cancel);
        _hooks.HoverPointChanged += (_, point) =>
        {
            if (Math.Abs(point.X - _lastPointer.X) > 1 || Math.Abs(point.Y - _lastPointer.Y) > 1)
            {
                _lastPointerChangedAtMs = _startedAt.ElapsedMilliseconds;
            }
            _lastPointer = point;
        };
        _hoverTimer = new DispatcherTimer { Interval = TimeSpan.FromMilliseconds(60) };
        _hoverTimer.Tick += (_, _) => HoverSample();
        _heartbeatTimer = new DispatcherTimer { Interval = TimeSpan.FromSeconds(1.5) };
        _heartbeatTimer.Tick += (_, _) => Jsonl.Emit("rpa_capture_assistant.heartbeat", new
        {
            ok = true,
            stage = "armed",
            backend = "windows_fla_ui_helper",
            nativeInspectorSessionId = _request.NativeInspectorSessionId,
            uptimeMs = _startedAt.ElapsedMilliseconds
        });
    }

    public int Run()
    {
        if (!_targetWindow.IsReady)
        {
            return 3;
        }

        var overlayReady = _overlay.ShowOverlay(_targetWindow);
        var hooksReady = _hooks.Install();
        var automationReady = _sampler.AutomationReady;
        Jsonl.Emit("rpa_capture_assistant.ready", new
        {
            ok = overlayReady && hooksReady && automationReady && _targetWindow.IsReady,
            backend = "windows_fla_ui_helper",
            captureBackend = "windows_fla_ui_helper",
            nativeInspectorSessionId = _request.NativeInspectorSessionId,
            recordingId = _request.RecordingId,
            stepId = _request.StepId,
            inputHookReady = hooksReady,
            mouseHookInstalled = _hooks.MouseHookInstalled,
            keyboardHookInstalled = _hooks.KeyboardHookInstalled,
            hotkeyRegistered = true,
            overlayReady,
            targetReady = _targetWindow.IsReady,
            automationReady,
            captureGesture = "LeftClick",
            alternateCaptureGesture = "Ctrl+LeftClick",
            cancelGesture = "Esc",
            targetWindow = _targetWindow.ToJson()
        });
        if (!overlayReady || !hooksReady || !automationReady)
        {
            Jsonl.Emit("rpa_capture_assistant.error", new
            {
                ok = false,
                stage = "readiness_failed",
                error = "Windows helper could not arm input hooks, overlay, or UI automation.",
                backend = "windows_fla_ui_helper",
                inputHookReady = hooksReady,
                mouseHookInstalled = _hooks.MouseHookInstalled,
                keyboardHookInstalled = _hooks.KeyboardHookInstalled,
                overlayReady,
                targetReady = _targetWindow.IsReady,
                automationReady
            });
            return 4;
        }

        _hoverTimer.Start();
        _heartbeatTimer.Start();
        var app = new System.Windows.Application { ShutdownMode = ShutdownMode.OnExplicitShutdown };
        app.Run();
        return _capturePosted ? 0 : 5;
    }

    private void HoverSample()
    {
        var now = _startedAt.ElapsedMilliseconds;
        var point = _lastPointer;
        if (now - _lastCrosshairAtMs >= CrosshairUpdateMinMs)
        {
            _overlay.UpdateCrosshair(point);
            _lastCrosshairAtMs = now;
        }
        if (!_targetWindow.Contains(point))
        {
            ShowTargetHintOnce(_targetWindow.Bounds, "Move inside target window", "outside_target");
            return;
        }
        var stable = now - _lastPointerChangedAtMs >= DeepHoverSampleDelayMs;
        var sample = stable ? _sampler.SampleElement(point) : _sampler.SampleLight(point);
        if (sample.Element is not null)
        {
            _hoverElement = sample.Element;
        }
        var mode = sample.Element is null ? "coordinate_or_window_fallback" : "uia_element";
        _overlay.Highlight(sample.Bounds, sample.Label, sample.Confidence, sample.Element is not null);
        EmitHoverChanged(point, sample, mode, now);
    }

    private void ShowTargetHintOnce(Rectangle bounds, string text, string signature)
    {
        if (_lastHintSignature == signature)
        {
            return;
        }
        _lastHintSignature = signature;
        _lastHoverSignature = "";
        _overlay.ShowTargetHint(bounds, text);
    }

    private void EmitHoverChanged(Point point, HoverSample sample, string mode, long now)
    {
        var signature = $"{mode}:{sample.Label}:{sample.Bounds.Left}:{sample.Bounds.Top}:{sample.Bounds.Width}:{sample.Bounds.Height}";
        if (signature == _lastHoverSignature && now - _lastHoverEmitAtMs < HoverEmitMinMs)
        {
            return;
        }
        _lastHoverSignature = signature;
        _lastHoverEmitAtMs = now;
        _lastHintSignature = "";
        Jsonl.Emit("rpa_capture_assistant.hover_changed", new
        {
            ok = true,
            backend = "windows_fla_ui_helper",
            nativeInspectorSessionId = _request.NativeInspectorSessionId,
            mode,
            coordinate = new { x = point.X, y = point.Y },
            targetWindow = _targetWindow.ToJson(),
            hoverSample = sample.ToJson()
        });
    }

    private void CaptureAt(Point point)
    {
        try
        {
            if (_capturePosted || _captureInFlight)
            {
                return;
            }
            _captureInFlight = true;
            if (!_targetWindow.Contains(point))
            {
                Jsonl.Emit("rpa_capture_assistant.error", new
                {
                    ok = false,
                    stage = "outside_target",
                    error = "Capture point is outside the locked target window.",
                    backend = "windows_fla_ui_helper",
                    coordinate = new { x = point.X, y = point.Y },
                    targetWindow = _targetWindow.ToJson()
                });
                _captureInFlight = false;
                return;
            }
            var capture = _sampler.Capture(point, _hoverElement);
            var screenshot = ScreenshotAnchor.CapturePatch(point, capture.Bounds, _targetWindow);
            var coordinateAnchor = CoordinateAnchor.FromPoint(point, _targetWindow);
            var payload = new Dictionary<string, object?>
            {
                ["event"] = "rpa_capture_assistant.captured",
                ["ok"] = true,
                ["action"] = _request.Action,
                ["source"] = "native_inspector",
                ["captureBackend"] = "windows_fla_ui_helper",
                ["nativeInspectorSessionId"] = _request.NativeInspectorSessionId,
                ["recordingId"] = _request.RecordingId,
                ["stepId"] = _request.StepId,
                ["targetStepId"] = _request.StepId,
                ["sourceStepId"] = _request.StepId,
                ["captureGesture"] = "LeftClick",
                ["alternateCaptureGesture"] = "Ctrl+LeftClick",
                ["coordinate"] = new { x = point.X, y = point.Y },
                ["targetWindow"] = _targetWindow.ToJson(),
                ["target"] = new
                {
                    window = _targetWindow.ToJson(),
                    selector = capture.SelectorCandidates.FirstOrDefault(),
                    spatialAnchor = new
                    {
                        coordinateAnchor,
                        imageAnchor = screenshot,
                        fallback = capture.SelectorCandidates.Count == 0,
                    }
                },
                ["selectorCandidates"] = capture.SelectorCandidates,
                ["hoverSample"] = capture.HoverSample,
                ["highlightBounds"] = RectJson.FromRectangle(capture.Bounds),
                ["coordinateAnchor"] = coordinateAnchor,
                ["windowRelativeCoordinate"] = new { x = coordinateAnchor.X, y = coordinateAnchor.Y },
                ["imageAnchor"] = screenshot,
                ["screenshotAnchor"] = screenshot,
                ["fragileCoordinateFallback"] = capture.SelectorCandidates.Count == 0,
                ["coordinateFallback"] = capture.SelectorCandidates.Count == 0,
                ["captureMode"] = _request.Mode,
                ["metadata"] = new
                {
                    nativeInspectorSessionId = _request.NativeInspectorSessionId,
                    captureBackend = "windows_fla_ui_helper",
                    selectorConfidence = capture.SelectorCandidates.FirstOrDefault()?.Confidence,
                    helper = "V8.Rpa.NativeInspector"
                }
            };
            var posted = EnginePoster.PostCapture(_request, payload);
            payload["postOk"] = posted.Ok;
            if (!posted.Ok)
            {
                payload["postError"] = posted.Error;
            }
            Jsonl.EmitRaw(payload);
            _capturePosted = true;
            System.Windows.Application.Current.Dispatcher.BeginInvoke(() => System.Windows.Application.Current.Shutdown());
        }
        catch (Exception ex)
        {
            _captureInFlight = false;
            Jsonl.Emit("rpa_capture_assistant.error", new
            {
                ok = false,
                stage = "capture_failed",
                error = ex.Message,
                detail = ex.ToString(),
                backend = "windows_fla_ui_helper"
            });
        }
    }

    private void Cancel()
    {
        Jsonl.Emit("rpa_capture_assistant.cancelled", new
        {
            ok = true,
            backend = "windows_fla_ui_helper",
            nativeInspectorSessionId = _request.NativeInspectorSessionId,
            recordingId = _request.RecordingId,
            stepId = _request.StepId
        });
        System.Windows.Application.Current.Dispatcher.BeginInvoke(() => System.Windows.Application.Current.Shutdown());
    }

    public void Dispose()
    {
        _hoverTimer.Stop();
        _heartbeatTimer.Stop();
        _hooks.Dispose();
        _sampler.Dispose();
        _overlay.Close();
    }
}

internal sealed class UiSampler : IDisposable
{
    private readonly NativeWindowInfo _targetWindow;
    private readonly AutomationBase? _automation;

    public UiSampler(NativeWindowInfo targetWindow)
    {
        _targetWindow = targetWindow;
        try
        {
            _automation = new UIA3Automation();
        }
        catch
        {
            try
            {
                _automation = new UIA2Automation();
            }
            catch
            {
                _automation = null;
            }
        }
    }

    public bool AutomationReady => _automation is not null;

    public HoverSample SampleLight(Point point)
    {
        var hwnd = NativeMethods.WindowFromPoint(point);
        var root = NativeMethods.GetAncestor(hwnd, NativeMethods.GA_ROOT);
        var rect = NativeMethods.GetWindowRect(root);
        var label = NativeMethods.GetWindowTitle(root);
        return new HoverSample(null, rect.IsEmpty ? _targetWindow.Bounds : rect, label, 0.38);
    }

    public HoverSample SampleElement(Point point)
    {
        try
        {
            var element = _automation?.FromPoint(point);
            return ToHoverSample(element, point);
        }
        catch
        {
            return SampleLight(point);
        }
    }

    public CaptureResult Capture(Point point, AutomationElement? existingElement)
    {
        var element = existingElement;
        try
        {
            element ??= _automation?.FromPoint(point);
        }
        catch
        {
            element = null;
        }
        var hover = ToHoverSample(element, point);
        var selectors = SelectorCandidate.FromElement(element);
        return new CaptureResult(selectors, hover.Bounds, hover.ToJson());
    }

    private HoverSample ToHoverSample(AutomationElement? element, Point point)
    {
        if (element is null)
        {
            return SampleLight(point);
        }
        try
        {
            var rect = element.BoundingRectangle;
            var bounds = rect.IsEmpty
                ? SampleLight(point).Bounds
                : new Rectangle(
                    Convert.ToInt32(Math.Round(Convert.ToDouble(rect.Left))),
                    Convert.ToInt32(Math.Round(Convert.ToDouble(rect.Top))),
                    Convert.ToInt32(Math.Round(Convert.ToDouble(rect.Width))),
                    Convert.ToInt32(Math.Round(Convert.ToDouble(rect.Height))));
            var label = element.Name;
            if (string.IsNullOrWhiteSpace(label))
            {
                label = element.ControlType.ToString();
            }
            return new HoverSample(element, bounds, label, 0.86);
        }
        catch
        {
            return SampleLight(point);
        }
    }

    public void Dispose()
    {
        _automation?.Dispose();
    }
}

internal sealed class InspectorOverlay : System.Windows.Window
{
    private readonly Canvas _canvas = new();
    private readonly System.Windows.Shapes.Rectangle _highlight = new();
    private readonly TextBlock _label = new();
    private readonly System.Windows.Shapes.Line _vertical = new();
    private readonly System.Windows.Shapes.Line _horizontal = new();

    public InspectorOverlay()
    {
        WindowStyle = WindowStyle.None;
        AllowsTransparency = true;
        Background = System.Windows.Media.Brushes.Transparent;
        Topmost = true;
        ShowInTaskbar = false;
        ResizeMode = ResizeMode.NoResize;
        Focusable = false;
        Content = _canvas;
        _highlight.Stroke = new SolidColorBrush(System.Windows.Media.Color.FromRgb(34, 211, 143));
        _highlight.StrokeThickness = 2;
        _highlight.Fill = new SolidColorBrush(System.Windows.Media.Color.FromArgb(28, 34, 211, 143));
        _highlight.RadiusX = 3;
        _highlight.RadiusY = 3;
        _label.Foreground = System.Windows.Media.Brushes.White;
        _label.Background = new SolidColorBrush(System.Windows.Media.Color.FromArgb(220, 10, 20, 36));
        _label.FontFamily = new System.Windows.Media.FontFamily("Segoe UI");
        _label.FontSize = 13;
        _label.Padding = new Thickness(8, 4, 8, 4);
        _vertical.Stroke = System.Windows.Media.Brushes.White;
        _vertical.StrokeDashArray = new DoubleCollection { 2, 3 };
        _vertical.Opacity = 0.55;
        _horizontal.Stroke = System.Windows.Media.Brushes.White;
        _horizontal.StrokeDashArray = new DoubleCollection { 2, 3 };
        _horizontal.Opacity = 0.55;
        _canvas.Children.Add(_highlight);
        _canvas.Children.Add(_label);
        _canvas.Children.Add(_vertical);
        _canvas.Children.Add(_horizontal);
    }

    public bool ShowOverlay(NativeWindowInfo targetWindow)
    {
        try
        {
            var screen = NativeMethods.VirtualScreen();
            Left = screen.Left;
            Top = screen.Top;
            Width = screen.Width;
            Height = screen.Height;
            Show();
            var hwnd = new WindowInteropHelper(this).Handle;
            NativeMethods.MakeClickThroughToolWindow(hwnd);
            ShowTargetHint(targetWindow.Bounds, "V8 RPA Inspector - click to capture, Esc to cancel");
            return true;
        }
        catch
        {
            return false;
        }
    }

    public void Highlight(Rectangle bounds, string label, double confidence, bool elementMatched)
    {
        Dispatcher.BeginInvoke(() =>
        {
            var left = bounds.Left - Left;
            var top = bounds.Top - Top;
            Canvas.SetLeft(_highlight, left);
            Canvas.SetTop(_highlight, top);
            _highlight.Width = Math.Max(4, bounds.Width);
            _highlight.Height = Math.Max(4, bounds.Height);
            _highlight.Stroke = new SolidColorBrush(elementMatched
                ? System.Windows.Media.Color.FromRgb(34, 211, 143)
                : System.Windows.Media.Color.FromRgb(250, 204, 21));
            _highlight.Fill = new SolidColorBrush(elementMatched
                ? System.Windows.Media.Color.FromArgb(32, 34, 211, 143)
                : System.Windows.Media.Color.FromArgb(30, 250, 204, 21));
            var modeLabel = elementMatched ? "UIA element" : "coordinate/image fallback";
            _label.Text = $"{label}  {Math.Round(confidence * 100)}%  -  {modeLabel} · LeftClick capture · Esc cancel";
            Canvas.SetLeft(_label, Math.Max(8, left));
            Canvas.SetTop(_label, Math.Max(8, top - 30));
        });
    }

    public void ShowTargetHint(Rectangle bounds, string text)
    {
        Dispatcher.BeginInvoke(() =>
        {
            Canvas.SetLeft(_highlight, bounds.Left - Left);
            Canvas.SetTop(_highlight, bounds.Top - Top);
            _highlight.Width = Math.Max(4, bounds.Width);
            _highlight.Height = Math.Max(4, bounds.Height);
            _label.Text = text;
            Canvas.SetLeft(_label, Math.Max(8, bounds.Left - Left));
            Canvas.SetTop(_label, Math.Max(8, bounds.Top - Top - 30));
        });
    }

    public void UpdateCrosshair(Point point)
    {
        Dispatcher.BeginInvoke(() =>
        {
            var x = point.X - Left;
            var y = point.Y - Top;
            _vertical.X1 = x;
            _vertical.X2 = x;
            _vertical.Y1 = 0;
            _vertical.Y2 = Height;
            _horizontal.X1 = 0;
            _horizontal.X2 = Width;
            _horizontal.Y1 = y;
            _horizontal.Y2 = y;
        });
    }
}

internal sealed class InputHooks : IDisposable
{
    private const int WH_KEYBOARD_LL = 13;
    private const int WH_MOUSE_LL = 14;
    private const int WM_KEYDOWN = 0x0100;
    private const int WM_SYSKEYDOWN = 0x0104;
    private const int WM_MOUSEMOVE = 0x0200;
    private const int WM_LBUTTONDOWN = 0x0201;
    private const int VK_ESCAPE = 0x1B;
    private const int HC_ACTION = 0;

    private readonly NativeMethods.LowLevelKeyboardProc _keyboardProc;
    private readonly NativeMethods.LowLevelMouseProc _mouseProc;
    private IntPtr _keyboardHook;
    private IntPtr _mouseHook;

    public event EventHandler<Point>? CaptureRequested;
    public event EventHandler? CancelRequested;
    public event EventHandler<Point>? HoverPointChanged;

    public InputHooks()
    {
        _keyboardProc = KeyboardCallback;
        _mouseProc = MouseCallback;
    }

    public bool MouseHookInstalled => _mouseHook != IntPtr.Zero;
    public bool KeyboardHookInstalled => _keyboardHook != IntPtr.Zero;

    public bool Install()
    {
        _keyboardHook = NativeMethods.SetWindowsHookEx(WH_KEYBOARD_LL, _keyboardProc, IntPtr.Zero, 0);
        _mouseHook = NativeMethods.SetWindowsHookEx(WH_MOUSE_LL, _mouseProc, IntPtr.Zero, 0);
        return MouseHookInstalled && KeyboardHookInstalled;
    }

    private IntPtr KeyboardCallback(int nCode, IntPtr wParam, IntPtr lParam)
    {
        if (nCode == HC_ACTION)
        {
            var message = wParam.ToInt32();
            var data = Marshal.PtrToStructure<NativeMethods.KbdLlHookStruct>(lParam);
            if ((message == WM_KEYDOWN || message == WM_SYSKEYDOWN) && data.vkCode == VK_ESCAPE)
            {
                CancelRequested?.Invoke(this, EventArgs.Empty);
                return new IntPtr(1);
            }
        }
        return NativeMethods.CallNextHookEx(_keyboardHook, nCode, wParam, lParam);
    }

    private IntPtr MouseCallback(int nCode, IntPtr wParam, IntPtr lParam)
    {
        if (nCode == HC_ACTION)
        {
            var data = Marshal.PtrToStructure<NativeMethods.MouseLlHookStruct>(lParam);
            var point = new Point(data.pt.x, data.pt.y);
            if (wParam.ToInt32() == WM_MOUSEMOVE)
            {
                HoverPointChanged?.Invoke(this, point);
            }
            if (wParam.ToInt32() == WM_LBUTTONDOWN)
            {
                CaptureRequested?.Invoke(this, point);
                return new IntPtr(1);
            }
        }
        return NativeMethods.CallNextHookEx(_mouseHook, nCode, wParam, lParam);
    }

    public void Dispose()
    {
        if (_mouseHook != IntPtr.Zero)
        {
            NativeMethods.UnhookWindowsHookEx(_mouseHook);
            _mouseHook = IntPtr.Zero;
        }
        if (_keyboardHook != IntPtr.Zero)
        {
            NativeMethods.UnhookWindowsHookEx(_keyboardHook);
            _keyboardHook = IntPtr.Zero;
        }
    }
}

internal sealed record InspectorRequest
{
    [JsonPropertyName("engineUrl")]
    public string EngineUrl { get; init; } = "http://127.0.0.1:9530";
    [JsonPropertyName("recordingId")]
    public string RecordingId { get; init; } = "";
    [JsonPropertyName("stepId")]
    public string StepId { get; init; } = "";
    [JsonPropertyName("action")]
    public string Action { get; init; } = "click";
    [JsonPropertyName("mode")]
    public string Mode { get; init; } = "capture_only";
    [JsonPropertyName("nativeInspectorSessionId")]
    public string NativeInspectorSessionId { get; init; } = $"native_inspector_{Guid.NewGuid():N}";
    [JsonPropertyName("targetWindow")]
    public Dictionary<string, object?> TargetWindow { get; init; } = new();
    [JsonPropertyName("targetProcess")]
    public Dictionary<string, object?> TargetProcess { get; init; } = new();
    [JsonPropertyName("appId")]
    public string AppId { get; init; } = "";

    public static InspectorRequest Load(string[] args)
    {
        string? requestJson = null;
        string? requestFile = null;
        for (var i = 0; i < args.Length; i++)
        {
            if (args[i] == "--request-json" && i + 1 < args.Length)
            {
                requestJson = Encoding.UTF8.GetString(Convert.FromBase64String(args[++i]));
            }
            else if (args[i] == "--request-file" && i + 1 < args.Length)
            {
                requestFile = args[++i];
            }
        }
        if (!string.IsNullOrWhiteSpace(requestFile))
        {
            requestJson = System.IO.File.ReadAllText(requestFile, Encoding.UTF8);
        }
        if (string.IsNullOrWhiteSpace(requestJson))
        {
            throw new InvalidOperationException("Missing --request-json or --request-file.");
        }
        var request = JsonSerializer.Deserialize<InspectorRequest>(requestJson, Jsonl.Options);
        if (request is null || string.IsNullOrWhiteSpace(request.RecordingId))
        {
            throw new InvalidOperationException("Invalid native inspector request.");
        }
        return request with { EngineUrl = request.EngineUrl.TrimEnd('/') };
    }
}

internal sealed record SelectorCandidate
{
    [JsonPropertyName("kind")]
    public string Kind { get; init; } = "accessibility";
    [JsonPropertyName("strategy")]
    public string Strategy { get; init; } = "uia";
    [JsonPropertyName("automationId")]
    public string? AutomationId { get; init; }
    [JsonPropertyName("name")]
    public string? Name { get; init; }
    [JsonPropertyName("controlType")]
    public string? ControlType { get; init; }
    [JsonPropertyName("className")]
    public string? ClassName { get; init; }
    [JsonPropertyName("frameworkId")]
    public string? FrameworkId { get; init; }
    [JsonPropertyName("bounds")]
    public object? Bounds { get; init; }
    [JsonPropertyName("confidence")]
    public double Confidence { get; init; }

    public static List<SelectorCandidate> FromElement(AutomationElement? element)
    {
        if (element is null)
        {
            return [];
        }
        try
        {
            var automationId = EmptyToNull(element.AutomationId);
            var name = EmptyToNull(element.Name);
            var className = EmptyToNull(element.ClassName);
            var frameworkId = EmptyToNull(element.FrameworkType.ToString());
            var controlType = element.ControlType.ToString();
            var hasStableSignal = automationId is not null || name is not null || className is not null;
            if (!hasStableSignal)
            {
                return [];
            }
            var rect = element.BoundingRectangle;
            var confidence = automationId is not null ? 0.92 : name is not null ? 0.78 : 0.58;
            return
            [
                new SelectorCandidate
                {
                    AutomationId = automationId,
                    Name = name,
                    ClassName = className,
                    FrameworkId = frameworkId,
                    ControlType = controlType,
                    Bounds = RectJson.FromFlaRect(rect),
                    Confidence = confidence
                }
            ];
        }
        catch
        {
            return [];
        }
    }

    private static string? EmptyToNull(string? value)
    {
        return string.IsNullOrWhiteSpace(value) ? null : value;
    }
}

internal sealed record CaptureResult(List<SelectorCandidate> SelectorCandidates, Rectangle Bounds, object HoverSample);

internal sealed record HoverSample(AutomationElement? Element, Rectangle Bounds, string Label, double Confidence)
{
    public object ToJson()
    {
        var element = Element is null ? null : new
        {
            name = Safe(() => Element.Name),
            automationId = Safe(() => Element.AutomationId),
            className = Safe(() => Element.ClassName),
            frameworkId = Safe(() => Element.FrameworkType.ToString()),
            controlType = Safe(() => Element.ControlType.ToString()),
            bounds = RectJson.FromRectangle(Bounds)
        };
        return new
        {
            element,
            label = Label,
            bounds = RectJson.FromRectangle(Bounds),
            confidence = Confidence
        };
    }

    private static string? Safe(Func<string?> read)
    {
        try
        {
            var value = read();
            return string.IsNullOrWhiteSpace(value) ? null : value;
        }
        catch
        {
            return null;
        }
    }
}

internal sealed record NativeWindowInfo(IntPtr Handle, int ProcessId, string ProcessName, string Title, Rectangle Bounds, Rectangle ClientRect, int Dpi)
{
    public bool IsReady => Handle != IntPtr.Zero && !Bounds.IsEmpty;

    public bool Contains(Point point)
    {
        return Bounds.Contains(point);
    }

    public object ToJson()
    {
        return new
        {
            handle = Handle.ToInt64(),
            processId = ProcessId,
            processName = ProcessName,
            title = Title,
            bounds = RectJson.FromRectangle(Bounds),
            clientRect = RectJson.FromRectangle(ClientRect.IsEmpty ? Bounds : ClientRect),
            dpi = Dpi
        };
    }

    public static NativeWindowInfo Resolve(InspectorRequest request)
    {
        var handle = ReadIntPtr(request.TargetWindow, "handle", "windowHandle");
        if (handle == IntPtr.Zero)
        {
            var processId = ReadInt(request.TargetWindow, "processId") ?? ReadInt(request.TargetProcess, "processId");
            var title = ReadString(request.TargetWindow, "title", "windowTitle") ?? request.AppId;
            handle = NativeMethods.FindBestWindow(processId, title);
        }
        return FromHandle(handle);
    }

    public static NativeWindowInfo FromHandle(IntPtr handle)
    {
        if (handle == IntPtr.Zero)
        {
            return new NativeWindowInfo(IntPtr.Zero, 0, "", "", Rectangle.Empty, Rectangle.Empty, 96);
        }
        NativeMethods.RestoreAndMaximize(handle);
        Thread.Sleep(180);
        var bounds = NativeMethods.GetWindowRect(handle);
        var client = NativeMethods.GetClientRectOnScreen(handle);
        NativeMethods.GetWindowThreadProcessId(handle, out var processId);
        var processName = "";
        try
        {
            processName = Process.GetProcessById((int)processId).ProcessName;
        }
        catch
        {
            processName = "";
        }
        return new NativeWindowInfo(handle, (int)processId, processName, NativeMethods.GetWindowTitle(handle), bounds, client, NativeMethods.GetDpiForWindowSafe(handle));
    }

    private static IntPtr ReadIntPtr(Dictionary<string, object?> data, params string[] keys)
    {
        foreach (var key in keys)
        {
            if (!data.TryGetValue(key, out var value) || value is null)
            {
                continue;
            }
            if (value is JsonElement json)
            {
                if (json.ValueKind == JsonValueKind.Number && json.TryGetInt64(out var longValue))
                {
                    return new IntPtr(longValue);
                }
                if (json.ValueKind == JsonValueKind.String && long.TryParse(json.GetString(), out longValue))
                {
                    return new IntPtr(longValue);
                }
            }
            if (long.TryParse(value.ToString(), out var parsed))
            {
                return new IntPtr(parsed);
            }
        }
        return IntPtr.Zero;
    }

    private static int? ReadInt(Dictionary<string, object?> data, params string[] keys)
    {
        foreach (var key in keys)
        {
            if (!data.TryGetValue(key, out var value) || value is null)
            {
                continue;
            }
            if (value is JsonElement json)
            {
                if (json.ValueKind == JsonValueKind.Number && json.TryGetInt32(out var intValue))
                {
                    return intValue;
                }
                if (json.ValueKind == JsonValueKind.String && int.TryParse(json.GetString(), out intValue))
                {
                    return intValue;
                }
            }
            if (int.TryParse(value.ToString(), out var parsed))
            {
                return parsed;
            }
        }
        return null;
    }

    private static string? ReadString(Dictionary<string, object?> data, params string[] keys)
    {
        foreach (var key in keys)
        {
            if (!data.TryGetValue(key, out var value) || value is null)
            {
                continue;
            }
            if (value is JsonElement json && json.ValueKind == JsonValueKind.String)
            {
                return json.GetString();
            }
            var text = value.ToString();
            if (!string.IsNullOrWhiteSpace(text))
            {
                return text;
            }
        }
        return null;
    }
}

internal sealed record CoordinateAnchor(double X, double Y, double RatioX, double RatioY, object WindowRect, object ClientRect, int Dpi, string MonitorId, double AbsoluteX, double AbsoluteY)
{
    public static CoordinateAnchor FromPoint(Point point, NativeWindowInfo window)
    {
        var client = window.ClientRect.IsEmpty ? window.Bounds : window.ClientRect;
        var x = point.X - client.Left;
        var y = point.Y - client.Top;
        var width = Math.Max(1, client.Width);
        var height = Math.Max(1, client.Height);
        return new CoordinateAnchor(
            Math.Round((double)x, 2),
            Math.Round((double)y, 2),
            Math.Round(Math.Clamp((double)x / width, 0, 1), 4),
            Math.Round(Math.Clamp((double)y / height, 0, 1), 4),
            RectJson.FromRectangle(window.Bounds),
            RectJson.FromRectangle(client),
            window.Dpi,
            NativeMethods.MonitorIdFromPoint(point),
            point.X,
            point.Y
        );
    }
}

internal static class ScreenshotAnchor
{
    public static object CapturePatch(Point point, Rectangle highlightBounds, NativeWindowInfo targetWindow)
    {
        try
        {
            var root = System.IO.Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.UserProfile), ".v8-agent-os", "rpa", "native-inspector", "captures");
            System.IO.Directory.CreateDirectory(root);
            var size = 220;
            var left = Math.Max(targetWindow.Bounds.Left, point.X - size / 2);
            var top = Math.Max(targetWindow.Bounds.Top, point.Y - size / 2);
            var right = Math.Min(targetWindow.Bounds.Right, left + size);
            var bottom = Math.Min(targetWindow.Bounds.Bottom, top + size);
            var rect = Rectangle.FromLTRB(left, top, Math.Max(left + 1, right), Math.Max(top + 1, bottom));
            using var bmp = new Bitmap(rect.Width, rect.Height);
            using var graphics = Graphics.FromImage(bmp);
            graphics.CopyFromScreen(rect.Left, rect.Top, 0, 0, rect.Size, CopyPixelOperation.SourceCopy);
            var file = System.IO.Path.Combine(root, $"capture-{DateTimeOffset.UtcNow.ToUnixTimeMilliseconds()}-{Guid.NewGuid():N}.png");
            bmp.Save(file, ImageFormat.Png);
            return new
            {
                screenshotPatchRef = file,
                bounds = RectJson.FromRectangle(rect),
                highlightBounds = RectJson.FromRectangle(highlightBounds),
                matchThreshold = 0.82,
                status = "ready"
            };
        }
        catch
        {
            return new
            {
                screenshotPatchRef = (string?)null,
                bounds = RectJson.FromRectangle(highlightBounds),
                matchThreshold = 0.82,
                status = "capture_failed"
            };
        }
    }
}

internal static class EnginePoster
{
    public static (bool Ok, string? Error) PostCapture(InspectorRequest request, Dictionary<string, object?> payload)
    {
        try
        {
            using var client = new HttpClient { Timeout = TimeSpan.FromSeconds(4) };
            var json = JsonSerializer.Serialize(payload, Jsonl.Options);
            var errors = new List<string>();
            foreach (var url in CandidateCaptureUrls(request))
            {
                try
                {
                    using var response = client.PostAsync(url, new StringContent(json, Encoding.UTF8, "application/json")).GetAwaiter().GetResult();
                    if (response.IsSuccessStatusCode)
                    {
                        return (true, null);
                    }
                    errors.Add($"{url}: {(int)response.StatusCode} {response.ReasonPhrase}");
                }
                catch (Exception ex)
                {
                    errors.Add($"{url}: {ex.Message}");
                }
            }
            return (false, string.Join("; ", errors));
        }
        catch (Exception ex)
        {
            return (false, ex.Message);
        }
    }

    private static IEnumerable<string> CandidateCaptureUrls(InspectorRequest request)
    {
        var baseUrl = (request.EngineUrl ?? "").Trim().TrimEnd('/');
        var recordingId = Uri.EscapeDataString(request.RecordingId);
        if (string.IsNullOrWhiteSpace(baseUrl))
        {
            yield break;
        }
        yield return $"{baseUrl}/rpa/recordings/{recordingId}/capture-assistant/capture";
        if (!baseUrl.EndsWith("/v1", StringComparison.OrdinalIgnoreCase))
        {
            yield return $"{baseUrl}/v1/rpa/recordings/{recordingId}/capture-assistant/capture";
        }
    }
}

internal static class Jsonl
{
    public static readonly JsonSerializerOptions Options = new()
    {
        PropertyNamingPolicy = JsonNamingPolicy.CamelCase,
        DefaultIgnoreCondition = JsonIgnoreCondition.WhenWritingNull,
        WriteIndented = false
    };

    private static readonly object LockObject = new();

    public static void Emit(string eventName, object payload)
    {
        var map = JsonSerializer.Deserialize<Dictionary<string, object?>>(JsonSerializer.Serialize(payload, Options), Options) ?? new Dictionary<string, object?>();
        map["event"] = eventName;
        EmitRaw(map);
    }

    public static void EmitRaw(Dictionary<string, object?> payload)
    {
        lock (LockObject)
        {
            Console.Out.WriteLine(JsonSerializer.Serialize(payload, Options));
            Console.Out.Flush();
        }
    }
}

internal static class RectJson
{
    public static object FromRectangle(Rectangle rect)
    {
        return new
        {
            left = rect.Left,
            top = rect.Top,
            right = rect.Right,
            bottom = rect.Bottom,
            width = Math.Max(0, rect.Width),
            height = Math.Max(0, rect.Height)
        };
    }

    public static object FromFlaRect(dynamic rect)
    {
        var left = Convert.ToDouble(rect.Left);
        var top = Convert.ToDouble(rect.Top);
        var right = Convert.ToDouble(rect.Right);
        var bottom = Convert.ToDouble(rect.Bottom);
        var width = Convert.ToDouble(rect.Width);
        var height = Convert.ToDouble(rect.Height);
        return new
        {
            left,
            top,
            right,
            bottom,
            width = Math.Max(0, width),
            height = Math.Max(0, height)
        };
    }
}

internal static class NativeMethods
{
    public const int GA_ROOT = 2;
    private const int SW_RESTORE = 9;
    private const int SW_MAXIMIZE = 3;
    private const int GWL_EXSTYLE = -20;
    private const int WS_EX_TRANSPARENT = 0x00000020;
    private const int WS_EX_TOOLWINDOW = 0x00000080;
    private const int WS_EX_NOACTIVATE = 0x08000000;

    public delegate IntPtr LowLevelKeyboardProc(int nCode, IntPtr wParam, IntPtr lParam);
    public delegate IntPtr LowLevelMouseProc(int nCode, IntPtr wParam, IntPtr lParam);

    [StructLayout(LayoutKind.Sequential)]
    public struct PointStruct
    {
        public int x;
        public int y;
    }

    [StructLayout(LayoutKind.Sequential)]
    public struct MouseLlHookStruct
    {
        public PointStruct pt;
        public uint mouseData;
        public uint flags;
        public uint time;
        public IntPtr dwExtraInfo;
    }

    [StructLayout(LayoutKind.Sequential)]
    public struct KbdLlHookStruct
    {
        public uint vkCode;
        public uint scanCode;
        public uint flags;
        public uint time;
        public IntPtr dwExtraInfo;
    }

    [StructLayout(LayoutKind.Sequential)]
    public struct WinRect
    {
        public int Left;
        public int Top;
        public int Right;
        public int Bottom;
    }

    [DllImport("user32.dll")]
    public static extern short GetAsyncKeyState(int vKey);
    [DllImport("user32.dll", SetLastError = true)]
    public static extern IntPtr SetWindowsHookEx(int idHook, LowLevelKeyboardProc lpfn, IntPtr hMod, uint dwThreadId);
    [DllImport("user32.dll", SetLastError = true)]
    public static extern IntPtr SetWindowsHookEx(int idHook, LowLevelMouseProc lpfn, IntPtr hMod, uint dwThreadId);
    [DllImport("user32.dll", SetLastError = true)]
    public static extern bool UnhookWindowsHookEx(IntPtr hhk);
    [DllImport("user32.dll")]
    public static extern IntPtr CallNextHookEx(IntPtr hhk, int nCode, IntPtr wParam, IntPtr lParam);
    [DllImport("user32.dll")]
    public static extern IntPtr WindowFromPoint(Point point);
    [DllImport("user32.dll")]
    public static extern IntPtr GetAncestor(IntPtr hwnd, int gaFlags);
    [DllImport("user32.dll", SetLastError = true)]
    public static extern bool GetWindowRect(IntPtr hWnd, out WinRect lpRect);
    [DllImport("user32.dll", SetLastError = true)]
    public static extern bool GetClientRect(IntPtr hWnd, out WinRect lpRect);
    [DllImport("user32.dll", SetLastError = true)]
    public static extern bool ClientToScreen(IntPtr hWnd, ref PointStruct lpPoint);
    [DllImport("user32.dll", SetLastError = true)]
    public static extern bool IsWindowVisible(IntPtr hWnd);
    [DllImport("user32.dll")]
    public static extern bool IsIconic(IntPtr hWnd);
    [DllImport("user32.dll")]
    public static extern bool ShowWindow(IntPtr hWnd, int nCmdShow);
    [DllImport("user32.dll")]
    public static extern bool BringWindowToTop(IntPtr hWnd);
    [DllImport("user32.dll")]
    public static extern bool SetForegroundWindow(IntPtr hWnd);
    [DllImport("user32.dll", SetLastError = true)]
    public static extern uint GetWindowThreadProcessId(IntPtr hWnd, out uint lpdwProcessId);
    [DllImport("user32.dll", CharSet = CharSet.Unicode)]
    public static extern int GetWindowText(IntPtr hWnd, StringBuilder text, int count);
    [DllImport("user32.dll", CharSet = CharSet.Unicode)]
    public static extern int GetClassName(IntPtr hWnd, StringBuilder text, int count);
    [DllImport("user32.dll")]
    public static extern IntPtr GetForegroundWindow();
    [DllImport("user32.dll")]
    public static extern int GetSystemMetrics(int nIndex);
    [DllImport("user32.dll")]
    public static extern int GetWindowLong(IntPtr hWnd, int nIndex);
    [DllImport("user32.dll")]
    public static extern int SetWindowLong(IntPtr hWnd, int nIndex, int dwNewLong);
    [DllImport("user32.dll")]
    public static extern uint GetDpiForWindow(IntPtr hwnd);
    [DllImport("user32.dll")]
    private static extern bool EnumWindows(EnumWindowsProc lpEnumFunc, IntPtr lParam);
    private delegate bool EnumWindowsProc(IntPtr hWnd, IntPtr lParam);

    public static void SetBestEffortDpiAwareness()
    {
        try
        {
            SetProcessDpiAwarenessContext(new IntPtr(-4));
            return;
        }
        catch { }
        try
        {
            SetProcessDPIAware();
        }
        catch { }
    }

    [DllImport("user32.dll")]
    private static extern bool SetProcessDPIAware();
    [DllImport("user32.dll")]
    private static extern bool SetProcessDpiAwarenessContext(IntPtr dpiContext);

    public static Rectangle GetWindowRect(IntPtr hwnd)
    {
        return GetWindowRect(hwnd, out var rect)
            ? Rectangle.FromLTRB(rect.Left, rect.Top, rect.Right, rect.Bottom)
            : Rectangle.Empty;
    }

    public static Rectangle GetClientRectOnScreen(IntPtr hwnd)
    {
        if (!GetClientRect(hwnd, out var rect))
        {
            return Rectangle.Empty;
        }
        var topLeft = new PointStruct { x = rect.Left, y = rect.Top };
        var bottomRight = new PointStruct { x = rect.Right, y = rect.Bottom };
        if (!ClientToScreen(hwnd, ref topLeft) || !ClientToScreen(hwnd, ref bottomRight))
        {
            return Rectangle.Empty;
        }
        return Rectangle.FromLTRB(topLeft.x, topLeft.y, bottomRight.x, bottomRight.y);
    }

    public static string GetWindowTitle(IntPtr hwnd)
    {
        var builder = new StringBuilder(512);
        _ = GetWindowText(hwnd, builder, builder.Capacity);
        return builder.ToString();
    }

    public static int GetDpiForWindowSafe(IntPtr hwnd)
    {
        try
        {
            var dpi = GetDpiForWindow(hwnd);
            return dpi == 0 ? 96 : (int)dpi;
        }
        catch
        {
            return 96;
        }
    }

    public static Point GetCursorPoint()
    {
        GetCursorPos(out var point);
        return new Point(point.x, point.y);
    }

    [DllImport("user32.dll")]
    private static extern bool GetCursorPos(out PointStruct lpPoint);

    public static Rect VirtualScreen()
    {
        var left = GetSystemMetrics(76); // SM_XVIRTUALSCREEN
        var top = GetSystemMetrics(77); // SM_YVIRTUALSCREEN
        var width = GetSystemMetrics(78); // SM_CXVIRTUALSCREEN
        var height = GetSystemMetrics(79); // SM_CYVIRTUALSCREEN
        return new Rect(left, top, Math.Max(1, width), Math.Max(1, height));
    }

    public static void MakeClickThroughToolWindow(IntPtr hwnd)
    {
        var style = GetWindowLong(hwnd, GWL_EXSTYLE);
        SetWindowLong(hwnd, GWL_EXSTYLE, style | WS_EX_TRANSPARENT | WS_EX_TOOLWINDOW | WS_EX_NOACTIVATE);
    }

    public static void RestoreAndMaximize(IntPtr hwnd)
    {
        try
        {
            if (IsIconic(hwnd))
            {
                ShowWindow(hwnd, SW_RESTORE);
            }
            ShowWindow(hwnd, SW_MAXIMIZE);
            BringWindowToTop(hwnd);
            SetForegroundWindow(hwnd);
        }
        catch { }
    }

    public static IntPtr FindBestWindow(int? processId, string? titleFragment)
    {
        var title = (titleFragment ?? "").Trim().ToLowerInvariant();
        var matches = new List<IntPtr>();
        EnumWindows((hwnd, _) =>
        {
            if (!IsWindowVisible(hwnd))
            {
                return true;
            }
            var rect = GetWindowRect(hwnd);
            if (rect.Width < 80 || rect.Height < 60)
            {
                return true;
            }
            GetWindowThreadProcessId(hwnd, out var pid);
            var windowTitle = GetWindowTitle(hwnd).ToLowerInvariant();
            var processMatch = processId is null || pid == processId.Value;
            var titleMatch = string.IsNullOrWhiteSpace(title) || windowTitle.Contains(title);
            if (processMatch && titleMatch)
            {
                matches.Add(hwnd);
            }
            return true;
        }, IntPtr.Zero);
        return matches.FirstOrDefault();
    }

    public static string MonitorIdFromPoint(Point point)
    {
        return $"screen:{point.X},{point.Y}";
    }
}
