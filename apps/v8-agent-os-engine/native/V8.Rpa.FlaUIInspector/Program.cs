using System.Diagnostics;
using System.Drawing;
using System.Drawing.Imaging;
using System.IO;
using System.Net.Http;
using System.Text;
using System.Text.Json;
using System.Text.Json.Serialization;
using System.Windows;
using System.Windows.Controls;
using System.Windows.Media;
using FlaUI.Core;
using FlaUI.Core.AutomationElements;
using FlaUI.UIA3;
using Application = System.Windows.Application;
using Brushes = System.Windows.Media.Brushes;
using Button = System.Windows.Controls.Button;
using Color = System.Windows.Media.Color;
using Label = System.Windows.Controls.Label;
using MediaFontFamily = System.Windows.Media.FontFamily;
using Point = System.Drawing.Point;
using Rect = System.Windows.Rect;
using WpfGrid = System.Windows.Controls.Grid;
using WpfListBox = System.Windows.Controls.ListBox;
using WpfTextBox = System.Windows.Controls.TextBox;
using Window = System.Windows.Window;

namespace V8.Rpa.FlaUIInspector;

internal static class Program
{
    [STAThread]
    private static int Main(string[] args)
    {
        try
        {
            var request = InspectorRequest.Load(args);
            if (!OperatingSystem.IsWindows())
            {
                Console.Error.WriteLine("V8.Rpa.FlaUIInspector is Windows-only.");
                return 2;
            }
            using var automation = new UIA3Automation();
            var app = new Application { ShutdownMode = ShutdownMode.OnMainWindowClose };
            var window = new InspectorWindow(request, automation);
            _ = EnginePoster.PostEvent(request, "ready", new Dictionary<string, object?>
            {
                ["sidecar"] = new Dictionary<string, object?>
                {
                    ["kind"] = "flaui_inspector_panel",
                    ["status"] = "ready"
                }
            });
            app.Run(window);
            _ = EnginePoster.PostEvent(request, "closed", new Dictionary<string, object?>
            {
                ["sidecar"] = new Dictionary<string, object?>
                {
                    ["kind"] = "flaui_inspector_panel",
                    ["status"] = "closed"
                }
            });
            return 0;
        }
        catch (Exception ex)
        {
            Console.Error.WriteLine(ex);
            return 1;
        }
    }
}

internal sealed class InspectorWindow : Window
{
    private readonly InspectorRequest _request;
    private readonly UIA3Automation _automation;
    private readonly WpfListBox _windowList = new();
    private readonly TreeView _elementTree = new();
    private readonly WpfTextBox _properties = new();
    private readonly TextBlock _status = new();
    private readonly HighlightOverlay _overlay = new();
    private WindowSnapshot? _selectedWindow;
    private ElementSnapshot? _selectedElement;

    public InspectorWindow(InspectorRequest request, UIA3Automation automation)
    {
        _request = request;
        _automation = automation;
        Title = "V8 RPA FlaUI Inspector";
        Width = 1180;
        Height = 760;
        MinWidth = 980;
        MinHeight = 620;
        WindowStartupLocation = WindowStartupLocation.CenterScreen;
        Background = Brushes.White;
        Content = BuildLayout();
        Loaded += (_, _) => RefreshWindows();
        Closed += (_, _) => _overlay.Close();
    }

    private UIElement BuildLayout()
    {
        var root = new DockPanel();
        var toolbar = new StackPanel
        {
            Orientation = Orientation.Horizontal,
            Margin = new Thickness(12),
        };
        toolbar.Children.Add(MakeButton("Refresh windows", (_, _) => RefreshWindows()));
        toolbar.Children.Add(MakeButton("Refresh elements", (_, _) => LoadSelectedWindowElements()));
        toolbar.Children.Add(MakeButton("Highlight", (_, _) => HighlightSelected()));
        toolbar.Children.Add(MakeButton("Test locator", (_, _) => TestSelectedLocator()));
        toolbar.Children.Add(MakeButton("Send to V8", (_, _) => SendSelected()));
        _status.Margin = new Thickness(12, 0, 12, 10);
        _status.Foreground = Brushes.DimGray;
        DockPanel.SetDock(toolbar, Dock.Top);
        DockPanel.SetDock(_status, Dock.Bottom);
        root.Children.Add(toolbar);
        root.Children.Add(_status);

        var grid = new WpfGrid { Margin = new Thickness(12, 0, 12, 12) };
        grid.ColumnDefinitions.Add(new ColumnDefinition { Width = new GridLength(280) });
        grid.ColumnDefinitions.Add(new ColumnDefinition { Width = new GridLength(1, GridUnitType.Star) });
        grid.ColumnDefinitions.Add(new ColumnDefinition { Width = new GridLength(360) });
        grid.RowDefinitions.Add(new RowDefinition { Height = GridLength.Auto });
        grid.RowDefinitions.Add(new RowDefinition { Height = new GridLength(1, GridUnitType.Star) });

        AddHeader(grid, "Windows", 0);
        AddHeader(grid, "Elements", 1);
        AddHeader(grid, "Properties / Locator", 2);

        _windowList.DisplayMemberPath = "Label";
        _windowList.SelectionChanged += (_, _) =>
        {
            _selectedWindow = _windowList.SelectedItem as WindowSnapshot;
            LoadSelectedWindowElements();
        };
        WpfGrid.SetColumn(_windowList, 0);
        WpfGrid.SetRow(_windowList, 1);
        grid.Children.Add(Wrap(_windowList));

        _elementTree.SelectedItemChanged += (_, args) =>
        {
            if (args.NewValue is TreeViewItem item && item.Tag is ElementSnapshot snapshot)
            {
                _selectedElement = snapshot;
                _properties.Text = JsonSerializer.Serialize(snapshot.ToCandidate(_selectedWindow, 0, null), Json.Options);
                HighlightSelected();
            }
        };
        WpfGrid.SetColumn(_elementTree, 1);
        WpfGrid.SetRow(_elementTree, 1);
        grid.Children.Add(Wrap(_elementTree));

        _properties.FontFamily = new MediaFontFamily("Consolas");
        _properties.FontSize = 12;
        _properties.AcceptsReturn = true;
        _properties.AcceptsTab = true;
        _properties.TextWrapping = TextWrapping.NoWrap;
        _properties.VerticalScrollBarVisibility = ScrollBarVisibility.Auto;
        _properties.HorizontalScrollBarVisibility = ScrollBarVisibility.Auto;
        WpfGrid.SetColumn(_properties, 2);
        WpfGrid.SetRow(_properties, 1);
        grid.Children.Add(_properties);

        root.Children.Add(grid);
        return root;
    }

    private static void AddHeader(WpfGrid grid, string text, int column)
    {
        var label = new Label
        {
            Content = text,
            FontWeight = FontWeights.SemiBold,
            Padding = new Thickness(4, 0, 0, 8)
        };
        WpfGrid.SetColumn(label, column);
        WpfGrid.SetRow(label, 0);
        grid.Children.Add(label);
    }

    private static ScrollViewer Wrap(UIElement child)
    {
        return new ScrollViewer
        {
            Content = child,
            VerticalScrollBarVisibility = ScrollBarVisibility.Auto,
            HorizontalScrollBarVisibility = ScrollBarVisibility.Auto,
            BorderBrush = new SolidColorBrush(Color.FromRgb(220, 224, 230)),
            BorderThickness = new Thickness(1),
            Padding = new Thickness(6),
        };
    }

    private static Button MakeButton(string text, RoutedEventHandler handler)
    {
        var button = new Button
        {
            Content = text,
            Margin = new Thickness(0, 0, 8, 0),
            Padding = new Thickness(12, 6, 12, 6),
        };
        button.Click += handler;
        return button;
    }

    private void RefreshWindows()
    {
        try
        {
            _windowList.ItemsSource = WindowSnapshot.Find(_automation, _request).ToList();
            _status.Text = $"Loaded {_windowList.Items.Count} windows. Select a window to inspect.";
        }
        catch (Exception ex)
        {
            _status.Text = $"Window refresh failed: {ex.Message}";
        }
    }

    private void LoadSelectedWindowElements()
    {
        _elementTree.Items.Clear();
        _selectedElement = null;
        if (_selectedWindow is null)
        {
            _status.Text = "Select a target window first.";
            return;
        }
        try
        {
            var rootElement = _automation.FromHandle(new IntPtr(_selectedWindow.Handle));
            var root = ElementSnapshot.FromElement(rootElement, 0);
            var rootItem = BuildTreeItem(root, rootElement, 0);
            rootItem.IsExpanded = true;
            _elementTree.Items.Add(rootItem);
            _status.Text = $"Loaded element tree for {_selectedWindow.Title}.";
        }
        catch (Exception ex)
        {
            _status.Text = $"Element refresh failed: {ex.Message}";
        }
    }

    private TreeViewItem BuildTreeItem(ElementSnapshot snapshot, AutomationElement element, int depth)
    {
        var item = new TreeViewItem
        {
            Header = snapshot.Label,
            Tag = snapshot,
        };
        if (depth >= 5)
        {
            return item;
        }
        foreach (var child in SafeChildren(element).Take(80))
        {
            try
            {
                item.Items.Add(BuildTreeItem(ElementSnapshot.FromElement(child, depth + 1), child, depth + 1));
            }
            catch
            {
                // Skip inaccessible automation children.
            }
        }
        return item;
    }

    private static IEnumerable<AutomationElement> SafeChildren(AutomationElement element)
    {
        try
        {
            return element.FindAllChildren() ?? [];
        }
        catch
        {
            return [];
        }
    }

    private void HighlightSelected()
    {
        if (_selectedElement is null)
        {
            _status.Text = "Select an element first.";
            return;
        }
        _overlay.Highlight(_selectedElement.Bounds, _selectedElement.Label);
        _status.Text = $"Highlighted {_selectedElement.Label}.";
    }

    private void TestSelectedLocator()
    {
        if (_selectedWindow is null || _selectedElement is null)
        {
            _status.Text = "Select a window and element first.";
            return;
        }
        var count = CountMatches(_selectedWindow, _selectedElement);
        _properties.Text = JsonSerializer.Serialize(_selectedElement.ToCandidate(_selectedWindow, count, null), Json.Options);
        _status.Text = count == 1 ? "Locator is unique." : $"Locator matched {count} elements.";
    }

    private void SendSelected()
    {
        if (_selectedWindow is null || _selectedElement is null)
        {
            _status.Text = "Select a window and element first.";
            return;
        }
        try
        {
            var count = CountMatches(_selectedWindow, _selectedElement);
            var screenshot = ScreenshotProof.Capture(_selectedElement.Bounds);
            var candidate = _selectedElement.ToCandidate(_selectedWindow, count, screenshot);
            var payload = new Dictionary<string, object?>
            {
                ["candidate"] = candidate,
                ["sidecar"] = new Dictionary<string, object?>
                {
                    ["kind"] = "flaui_inspector_panel",
                    ["status"] = "candidate_sent"
                }
            };
            var result = EnginePoster.PostEvent(_request, "candidate", payload);
            _status.Text = result.Ok ? "Candidate sent to V8 capture pool." : $"Send failed: {result.Error}";
        }
        catch (Exception ex)
        {
            _status.Text = $"Send failed: {ex.Message}";
        }
    }

    private int CountMatches(WindowSnapshot window, ElementSnapshot selected)
    {
        try
        {
            var rootElement = _automation.FromHandle(new IntPtr(window.Handle));
            return Enumerate(rootElement, 0)
                .Select(element => ElementSnapshot.FromElement(element, 0))
                .Count(candidate => selected.Matches(candidate));
        }
        catch
        {
            return 0;
        }
    }

    private static IEnumerable<AutomationElement> Enumerate(AutomationElement root, int depth)
    {
        yield return root;
        if (depth >= 8) yield break;
        foreach (var child in SafeChildren(root).Take(300))
        {
            foreach (var item in Enumerate(child, depth + 1))
            {
                yield return item;
            }
        }
    }
}

internal sealed record WindowSnapshot(long Handle, int ProcessId, string ProcessName, string Title, BoundsJson Bounds)
{
    public string Label => string.IsNullOrWhiteSpace(Title) ? $"{ProcessName} ({Handle})" : $"{Title} · {ProcessName}";

    public static IEnumerable<WindowSnapshot> Find(UIA3Automation automation, InspectorRequest request)
    {
        var desktop = automation.GetDesktop();
        var windows = desktop.FindAllChildren(automation.ConditionFactory.ByControlType(FlaUI.Core.Definitions.ControlType.Window));
        var targetLock = request.TargetLock ?? new Dictionary<string, object?>();
        var titleHint = ReadString(targetLock, "windowTitle", "title", "label").ToLowerInvariant();
        var handleHint = ReadLong(targetLock, "handle", "windowHandle");
        foreach (var window in windows)
        {
            WindowSnapshot? snapshot = null;
            try
            {
                var native = window.Properties.NativeWindowHandle.ValueOrDefault;
                var processId = window.Properties.ProcessId.ValueOrDefault;
                var process = SafeProcessName(processId);
                snapshot = new WindowSnapshot(
                    native,
                    processId,
                    process,
                    Safe(() => window.Name) ?? "",
                    BoundsJson.FromFla(window.BoundingRectangle)
                );
            }
            catch
            {
                // Ignore inaccessible windows.
            }
            if (snapshot is null || snapshot.Handle == 0 || string.IsNullOrWhiteSpace(snapshot.Title))
            {
                continue;
            }
            if (handleHint is not null && snapshot.Handle != handleHint.Value)
            {
                continue;
            }
            if (!string.IsNullOrWhiteSpace(titleHint) && !snapshot.Label.ToLowerInvariant().Contains(titleHint))
            {
                continue;
            }
            yield return snapshot;
        }
    }

    public Dictionary<string, object?> ToJson()
    {
        return new Dictionary<string, object?>
        {
            ["handle"] = Handle,
            ["processId"] = ProcessId,
            ["processName"] = ProcessName,
            ["title"] = Title,
            ["bounds"] = Bounds
        };
    }

    private static string SafeProcessName(int processId)
    {
        try
        {
            return Process.GetProcessById(processId).ProcessName;
        }
        catch
        {
            return "";
        }
    }

    private static string ReadString(Dictionary<string, object?> payload, params string[] keys)
    {
        foreach (var key in keys)
        {
            if (payload.TryGetValue(key, out var value) && value is not null)
            {
                var text = Convert.ToString(value) ?? "";
                if (!string.IsNullOrWhiteSpace(text)) return text;
            }
        }
        return "";
    }

    private static long? ReadLong(Dictionary<string, object?> payload, params string[] keys)
    {
        foreach (var key in keys)
        {
            if (!payload.TryGetValue(key, out var value) || value is null) continue;
            if (long.TryParse(Convert.ToString(value), out var parsed)) return parsed;
        }
        return null;
    }

    private static string? Safe(Func<string> read)
    {
        try
        {
            return read();
        }
        catch
        {
            return null;
        }
    }
}

internal sealed record ElementSnapshot(
    string Label,
    string? AutomationId,
    string? Name,
    string? ControlType,
    string? ClassName,
    string? FrameworkId,
    BoundsJson Bounds,
    IReadOnlyList<string> Patterns,
    int Depth)
{
    public static ElementSnapshot FromElement(AutomationElement element, int depth)
    {
        var name = EmptyToNull(Safe(() => element.Name));
        var automationId = EmptyToNull(Safe(() => element.AutomationId));
        var controlType = EmptyToNull(Safe(() => element.ControlType.ToString()));
        var className = EmptyToNull(Safe(() => element.ClassName));
        var frameworkId = EmptyToNull(Safe(() => element.FrameworkType.ToString()));
        var label = name ?? automationId ?? controlType ?? "element";
        return new ElementSnapshot(
            $"{label} · {controlType ?? "control"}",
            automationId,
            name,
            controlType,
            className,
            frameworkId,
            BoundsJson.FromFla(element.BoundingRectangle),
            PatternReader.Read(element),
            depth
        );
    }

    public bool Matches(ElementSnapshot candidate)
    {
        if (!string.IsNullOrWhiteSpace(AutomationId))
        {
            return AutomationId == candidate.AutomationId && (ControlType is null || ControlType == candidate.ControlType);
        }
        if (!string.IsNullOrWhiteSpace(Name))
        {
            return Name == candidate.Name && (ControlType is null || ControlType == candidate.ControlType);
        }
        return !string.IsNullOrWhiteSpace(ClassName) && ClassName == candidate.ClassName && ControlType == candidate.ControlType;
    }

    public Dictionary<string, object?> ToCandidate(WindowSnapshot? window, int findCount, ScreenshotProof? screenshot)
    {
        var primary = new Dictionary<string, object?>
        {
            ["strategy"] = "uia",
            ["automationId"] = AutomationId,
            ["name"] = Name,
            ["controlType"] = ControlType,
            ["className"] = ClassName,
            ["frameworkId"] = FrameworkId,
            ["patterns"] = Patterns,
            ["bounds"] = Bounds,
            ["confidence"] = AutomationId is not null ? 0.92 : Name is not null ? 0.78 : 0.58
        }.Where(pair => pair.Value is not null).ToDictionary(pair => pair.Key, pair => pair.Value);
        return new Dictionary<string, object?>
        {
            ["label"] = Name ?? AutomationId ?? ControlType ?? "Windows element",
            ["source"] = "flaui_inspector_panel",
            ["platform"] = "windows",
            ["selector"] = primary,
            ["selectorCandidates"] = new[] { primary },
            ["targetWindow"] = window?.ToJson() ?? new Dictionary<string, object?>(),
            ["anchorBundle"] = new Dictionary<string, object?>
            {
                ["window"] = window?.ToJson() ?? new Dictionary<string, object?>(),
                ["rect"] = Bounds,
                ["screenshotAnchor"] = screenshot?.ToJson()
            },
            ["locatorBundle"] = new Dictionary<string, object?>
            {
                ["platform"] = "windows",
                ["primaryLocator"] = primary,
                ["alternateLocators"] = Array.Empty<object>(),
                ["searchScope"] = window?.ToJson() ?? new Dictionary<string, object?>(),
                ["uniqueness"] = new Dictionary<string, object?>
                {
                    ["count"] = findCount,
                    ["source"] = "flaui_inspector_panel"
                },
                ["confidence"] = primary.TryGetValue("confidence", out var confidence) ? confidence : 0.58,
                ["source"] = "flaui_inspector_panel"
            },
            ["proof"] = new Dictionary<string, object?>
            {
                ["status"] = findCount == 1 && screenshot is not null ? "verified" : findCount > 1 ? "locator_ambiguous" : "locator_unresolved",
                ["findCount"] = findCount,
                ["highlightRef"] = screenshot?.Path,
                ["screenshotRef"] = screenshot?.Path,
                ["warnings"] = screenshot is null ? new[] { "screenshot proof unavailable" } : Array.Empty<string>(),
                ["verifiedAt"] = DateTimeOffset.UtcNow.ToString("O"),
                ["verifier"] = "flaui_inspector_panel"
            },
            ["metadata"] = new Dictionary<string, object?>
            {
                ["patterns"] = Patterns,
                ["depth"] = Depth
            }
        };
    }

    private static string? EmptyToNull(string? value)
    {
        return string.IsNullOrWhiteSpace(value) ? null : value;
    }

    private static string? Safe(Func<string?> read)
    {
        try
        {
            return read();
        }
        catch
        {
            return null;
        }
    }
}

internal sealed class HighlightOverlay : Window
{
    private readonly Border _border;
    private readonly TextBlock _label;

    public HighlightOverlay()
    {
        WindowStyle = WindowStyle.None;
        AllowsTransparency = true;
        Background = Brushes.Transparent;
        Topmost = true;
        ShowInTaskbar = false;
        IsHitTestVisible = false;
        _label = new TextBlock
        {
            Background = new SolidColorBrush(Color.FromRgb(8, 17, 31)),
            Foreground = Brushes.White,
            Padding = new Thickness(6, 2, 6, 2),
            FontSize = 12
        };
        _border = new Border
        {
            BorderBrush = new SolidColorBrush(Color.FromRgb(34, 211, 238)),
            BorderThickness = new Thickness(3),
            Child = _label
        };
        Content = _border;
    }

    public void Highlight(BoundsJson bounds, string label)
    {
        Left = bounds.Left;
        Top = bounds.Top;
        Width = Math.Max(32, bounds.Width);
        Height = Math.Max(24, bounds.Height);
        _label.Text = label;
        if (!IsVisible) Show();
    }
}

internal sealed record ScreenshotProof(string Path, BoundsJson Bounds)
{
    public static ScreenshotProof? Capture(BoundsJson bounds)
    {
        try
        {
            var root = System.IO.Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.UserProfile), ".v8-agent-os", "rpa", "flaui-inspector", "proof");
            Directory.CreateDirectory(root);
            var width = Math.Max(1, (int)Math.Round(bounds.Width));
            var height = Math.Max(1, (int)Math.Round(bounds.Height));
            using var bitmap = new Bitmap(width, height);
            using var graphics = Graphics.FromImage(bitmap);
            graphics.CopyFromScreen(new Point((int)Math.Round(bounds.Left), (int)Math.Round(bounds.Top)), Point.Empty, new System.Drawing.Size(width, height));
            var file = System.IO.Path.Combine(root, $"proof-{DateTimeOffset.UtcNow.ToUnixTimeMilliseconds()}-{Guid.NewGuid():N}.png");
            bitmap.Save(file, ImageFormat.Png);
            return new ScreenshotProof(file, bounds);
        }
        catch
        {
            return null;
        }
    }

    public Dictionary<string, object?> ToJson()
    {
        return new Dictionary<string, object?>
        {
            ["screenshotRef"] = Path,
            ["bounds"] = Bounds,
            ["status"] = "ready"
        };
    }
}

internal sealed record BoundsJson(double Left, double Top, double Width, double Height)
{
    public static BoundsJson FromFla(dynamic rect)
    {
        return new BoundsJson(rect.Left, rect.Top, rect.Width, rect.Height);
    }
}

internal static class PatternReader
{
    public static IReadOnlyList<string> Read(AutomationElement element)
    {
        try
        {
            var patterns = element.Patterns;
            return patterns.GetType()
                .GetProperties()
                .Where(property => property.GetIndexParameters().Length == 0)
                .Select(property => new { property.Name, Value = Safe(() => property.GetValue(patterns)) })
                .Where(item => item.Value is not null && IsSupported(item.Value))
                .Select(item => item.Name)
                .Take(32)
                .ToArray();
        }
        catch
        {
            return [];
        }
    }

    private static object? Safe(Func<object?> read)
    {
        try
        {
            return read();
        }
        catch
        {
            return null;
        }
    }

    private static bool IsSupported(object value)
    {
        try
        {
            var property = value.GetType().GetProperty("IsSupported");
            return property?.GetValue(value) is true;
        }
        catch
        {
            return false;
        }
    }
}

internal sealed class InspectorRequest
{
    [JsonPropertyName("sessionId")]
    public string SessionId { get; init; } = "";
    [JsonPropertyName("recordingId")]
    public string RecordingId { get; init; } = "";
    [JsonPropertyName("oneTimeToken")]
    public string OneTimeToken { get; init; } = "";
    [JsonPropertyName("engineUrl")]
    public string EngineUrl { get; init; } = "http://127.0.0.1:9530";
    [JsonPropertyName("callback")]
    public CallbackInfo Callback { get; init; } = new();
    [JsonPropertyName("targetLock")]
    public Dictionary<string, object?> TargetLock { get; init; } = new();

    public static InspectorRequest Load(string[] args)
    {
        var requestFile = "";
        for (var index = 0; index < args.Length - 1; index++)
        {
            if (args[index] == "--request-file")
            {
                requestFile = args[index + 1];
                break;
            }
        }
        if (string.IsNullOrWhiteSpace(requestFile))
        {
            throw new InvalidOperationException("--request-file is required.");
        }
        var json = File.ReadAllText(requestFile, Encoding.UTF8);
        return JsonSerializer.Deserialize<InspectorRequest>(json, Json.Options) ?? throw new InvalidOperationException("Invalid inspector request.");
    }
}

internal sealed class CallbackInfo
{
    [JsonPropertyName("url")]
    public string? Url { get; init; }
    [JsonPropertyName("path")]
    public string? Path { get; init; }
}

internal static class EnginePoster
{
    public static (bool Ok, string? Error) PostEvent(InspectorRequest request, string type, Dictionary<string, object?> payload)
    {
        try
        {
            var url = CallbackUrl(request);
            var body = new Dictionary<string, object?>(payload)
            {
                ["type"] = type,
                ["oneTimeToken"] = request.OneTimeToken
            };
            using var client = new HttpClient { Timeout = TimeSpan.FromSeconds(5) };
            var json = JsonSerializer.Serialize(body, Json.Options);
            using var response = client.PostAsync(url, new StringContent(json, Encoding.UTF8, "application/json")).GetAwaiter().GetResult();
            return response.IsSuccessStatusCode ? (true, null) : (false, $"{(int)response.StatusCode} {response.ReasonPhrase}");
        }
        catch (Exception ex)
        {
            return (false, ex.Message);
        }
    }

    private static string CallbackUrl(InspectorRequest request)
    {
        if (!string.IsNullOrWhiteSpace(request.Callback.Url))
        {
            return request.Callback.Url!;
        }
        var baseUrl = (request.EngineUrl ?? "http://127.0.0.1:9530").TrimEnd('/');
        return $"{baseUrl}{request.Callback.Path}";
    }
}

internal static class Json
{
    public static readonly JsonSerializerOptions Options = new()
    {
        PropertyNamingPolicy = JsonNamingPolicy.CamelCase,
        DefaultIgnoreCondition = JsonIgnoreCondition.WhenWritingNull,
        WriteIndented = true
    };
}
