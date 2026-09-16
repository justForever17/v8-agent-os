"""Compile and exercise the production native sender without a desktop or network."""
from __future__ import annotations

import argparse
import os
from pathlib import Path
import subprocess
import tempfile


HARNESS = r'''
internal sealed class FixtureHandler(Func<string, CancellationToken, Task<HttpResponseMessage>> send) : HttpMessageHandler
{
    public readonly List<string> Bodies = new();
    private int active;
    public int MaxActive;
    protected override async Task<HttpResponseMessage> SendAsync(HttpRequestMessage request, CancellationToken cancellationToken)
    {
        var body = await request.Content!.ReadAsStringAsync(cancellationToken);
        lock (Bodies) Bodies.Add(body);
        var current = Interlocked.Increment(ref active);
        MaxActive = Math.Max(MaxActive, current);
        try { return await send(body, cancellationToken); }
        finally { Interlocked.Decrement(ref active); }
    }
}

internal static class ContractMain
{
    private static HttpResponseMessage Reply(string body, int status = 200) => new((System.Net.HttpStatusCode)status) { Content = new StringContent(body) };
    private static InspectorRequest Request() => new() { OneTimeToken = "fixture-token", Generation = "fixture-generation", SessionId = "fixture-session", Callback = new CallbackInfo { Url = "http://fixture.invalid/events" } };
    private static void Check(bool value, string message) { if (!value) throw new Exception(message); }
    private static JsonElement Read(string body) => JsonDocument.Parse(body).RootElement.Clone();
    public static async Task Main()
    {
        var count = 0;
        var handler = new FixtureHandler((_, _) => ++count == 1 ? throw new HttpRequestException("fixture-token") : Task.FromResult(Reply("{\"ok\":true}")));
        using (var poster = new EnginePoster(Request(), handler))
        {
            var payload = new Dictionary<string, object?> { ["candidate"] = "immutable-candidate" };
            Check((await poster.PostEventAsync("candidate", payload)).Ok, "transport retry must deliver");
            Check(handler.Bodies.Count == 2 && handler.Bodies[0] == handler.Bodies[1], "retry must preserve eventId, sequence and payload");
            var first = Read(handler.Bodies[0]);
            Check(first.GetProperty("seq").GetInt64() == 1 && first.GetProperty("generation").GetString() == "fixture-generation", "session identity must accompany each event");
            Check((await poster.PostEventAsync("heartbeat", new())).Ok, "next event must deliver");
            var next = Read(handler.Bodies[2]);
            Check(next.GetProperty("seq").GetInt64() == 2 && first.GetProperty("eventId").GetString() != next.GetProperty("eventId").GetString(), "advance only after acknowledgement");
        }
        Console.WriteLine("PASS retry identity and acknowledgement sequencing");

        var acknowledged = false;
        handler = new FixtureHandler((_, _) => Task.FromResult(Reply(acknowledged ? "{\"ok\":true}" : "{}")));
        using (var poster = new EnginePoster(Request(), handler))
        {
            var payload = new Dictionary<string, object?> { ["candidate"] = "retained" };
            var failed = await poster.PostEventAsync("candidate", payload);
            Check(!failed.Ok && handler.Bodies.Count == 3, "HTTP 200 without an acknowledgement must remain pending");
            Check(!failed.Error!.Contains("fixture-token"), "failure must not expose credentials");
            acknowledged = true;
            Check((await poster.PostEventAsync("candidate", payload)).Ok, "explicit retry must resume pending event");
            Check(handler.Bodies.Distinct().Count() == 1, "manual retry must not create a new candidate event");
        }
        Console.WriteLine("PASS failed candidate retention and HTTP 200 rejection");

        var entered = new TaskCompletionSource(TaskCreationOptions.RunContinuationsAsynchronously);
        var release = new TaskCompletionSource(TaskCreationOptions.RunContinuationsAsynchronously);
        handler = new FixtureHandler(async (_, _) => { entered.TrySetResult(); await release.Task; return Reply("{\"ok\":true}"); });
        using (var poster = new EnginePoster(Request(), handler))
        {
            var first = poster.PostEventAsync("ready", new());
            await entered.Task;
            var second = poster.PostEventAsync("candidate", new());
            await Task.Delay(30);
            Check(handler.Bodies.Count == 1, "concurrent events must not send out of order");
            release.SetResult();
            Check((await first).Ok && (await second).Ok && handler.MaxActive == 1, "sender must remain single flight");
            Check(Read(handler.Bodies[1]).GetProperty("seq").GetInt64() == 2, "queued event must follow acknowledged sequence");
        }
        Console.WriteLine("PASS concurrent event ordering");

        entered = new TaskCompletionSource(TaskCreationOptions.RunContinuationsAsynchronously);
        handler = new FixtureHandler(async (_, cancellation) => { entered.TrySetResult(); await Task.Delay(Timeout.Infinite, cancellation); return Reply("{\"ok\":true}"); });
        using (var poster = new EnginePoster(Request(), handler))
        {
            var sending = poster.PostEventAsync("candidate", new());
            await entered.Task;
            poster.Stop();
            Check(!(await sending).Ok && !(await poster.PostEventAsync("heartbeat", new())).Ok, "stop must cancel in-flight and queued writes");
            Check(handler.Bodies.Count == 1, "stop must not start a retry");
        }
        Console.WriteLine("PASS stop during pending acknowledgement");

        handler = new FixtureHandler((_, _) => Task.FromResult(Reply("fixture-token", 403)));
        using (var poster = new EnginePoster(Request(), handler))
        {
            var rejected = await poster.PostEventAsync("candidate", new());
            Check(!rejected.Ok && poster.IsStopped && handler.Bodies.Count == 1, "revoked session must stop without retrying");
            Check(!rejected.Error!.Contains("fixture-token"), "error body must not leak secrets");
        }
        Console.WriteLine("PASS revoked session and sanitized failures");

        var requestFile = Path.Combine(Path.GetTempPath(), "v8-inspector-contract-" + Guid.NewGuid().ToString("N") + ".json");
        try
        {
            File.WriteAllText(requestFile, "{\"oneTimeToken\":\"fixture-token\",\"generation\":\"fixture-generation\",\"stopRequested\":false}");
            var request = InspectorRequest.Load(["--request-file", requestFile]);
            Check(!request.ShouldStop(), "active request must remain available");
            File.WriteAllText(requestFile, "{\"stopRequested\":true}");
            handler = new FixtureHandler((_, _) => throw new Exception("stopped request must not send"));
            using var poster = new EnginePoster(request, handler);
            Check(!(await poster.PostEventAsync("candidate", new())).Ok, "request stop flag must prevent sending");
            File.Delete(requestFile);
            Check(request.ShouldStop(), "revoked request file must stop the inspector");
        }
        finally { File.Delete(requestFile); }
        Console.WriteLine("PASS governed request-file stop");
    }
}
'''


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dotnet", required=True, help="Path to an installed .NET 8 SDK dotnet executable")
    args = parser.parse_args()
    engine = Path(__file__).resolve().parents[2]
    source = (engine / "native/V8.Rpa.FlaUIInspector/Program.cs").read_text(encoding="utf-8")
    sender = source[source.index("internal sealed class InspectorRequest"):]
    with tempfile.TemporaryDirectory(prefix="v8-flaui-callback-contract-") as temporary:
        project = Path(temporary)
        (project / "Contract.csproj").write_text('<Project Sdk="Microsoft.NET.Sdk"><PropertyGroup><OutputType>Exe</OutputType><TargetFramework>net8.0</TargetFramework><ImplicitUsings>enable</ImplicitUsings><Nullable>enable</Nullable></PropertyGroup></Project>', encoding="utf-8")
        (project / "Program.cs").write_text("using System.Text;\nusing System.Text.Json;\nusing System.Text.Json.Serialization;\n" + sender + HARNESS, encoding="utf-8")
        environment = {**os.environ, "DOTNET_CLI_HOME": str(project / "cli"), "DOTNET_CLI_TELEMETRY_OPTOUT": "1", "DOTNET_SKIP_FIRST_TIME_EXPERIENCE": "1", "DOTNET_GENERATE_ASPNET_CERTIFICATE": "false", "DOTNET_NOLOGO": "1"}
        subprocess.run([args.dotnet, "run", "--project", str(project / "Contract.csproj"), "--configuration", "Release", "--nologo"], check=True, env=environment)


if __name__ == "__main__":
    main()
