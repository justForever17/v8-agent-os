param(
    [string]$BaseUrl = $env:V8_OPENAI_COMPAT_BASE_URL,
    [string]$ApiKey = $env:V8_OPENAI_COMPAT_API_KEY,
    [switch]$RequireToolCall
)

$ErrorActionPreference = "Stop"

if (-not $BaseUrl -or -not $ApiKey) {
    Write-Host "SKIP: set V8_OPENAI_COMPAT_BASE_URL and V8_OPENAI_COMPAT_API_KEY to run the OpenAI compat smoke test."
    Write-Host "Example Base URL: http://localhost:9528/api/network-supervisor/openai/v1"
    exit 0
}

$BaseUrl = $BaseUrl.TrimEnd("/")
$headers = @{
    Authorization = "Bearer $ApiKey"
    "Content-Type" = "application/json"
}

Write-Host "[1/3] non-stream chat completions"
$chatBody = @{
    model = "gpt-4o"
    messages = @(@{ role = "user"; content = "Reply with the word pong." })
} | ConvertTo-Json -Depth 10 -Compress
$chat = Invoke-RestMethod -Method POST -Uri "$BaseUrl/chat/completions" -Headers $headers -Body $chatBody
if (-not $chat.id -or -not $chat.choices) {
    throw "Non-stream response does not look like an OpenAI chat completion."
}
Write-Host "OK: $($chat.id)"

Write-Host "[2/3] stream chat completions"
$streamBody = @{
    model = "gpt-4o"
    stream = $true
    messages = @(@{ role = "user"; content = "Reply with a short ping." })
} | ConvertTo-Json -Depth 10 -Compress
$stream = Invoke-WebRequest -Method POST -Uri "$BaseUrl/chat/completions" -Headers $headers -Body $streamBody
if ($stream.Content -notmatch "data:") {
    throw "Stream response did not contain SSE data frames."
}
Write-Host "OK: stream frames observed"

Write-Host "[3/3] external tool round-trip probe"
$toolBody = @{
    model = "gpt-4o"
    messages = @(@{ role = "user"; content = "Use the get_weather tool for Hangzhou, then answer." })
    tools = @(
        @{
            type = "function"
            function = @{
                name = "get_weather"
                description = "Get current weather for a city."
                parameters = @{
                    type = "object"
                    properties = @{ city = @{ type = "string" } }
                    required = @("city")
                }
            }
        }
    )
    tool_choice = "auto"
} | ConvertTo-Json -Depth 20 -Compress
$toolResponse = Invoke-RestMethod -Method POST -Uri "$BaseUrl/chat/completions" -Headers $headers -Body $toolBody
$toolCall = $toolResponse.choices[0].message.tool_calls | Select-Object -First 1
if (-not $toolCall) {
    if ($RequireToolCall) {
        throw "Model did not request the external get_weather tool."
    }
    Write-Host "SKIP: model did not emit a tool_call; fixture tests cover deterministic alias/wire mapping."
    exit 0
}

if ($toolCall.function.name -ne "get_weather") {
    throw "External tool name was not restored on the wire. Got: $($toolCall.function.name)"
}

$assistantMessage = @{
    role = "assistant"
    content = $null
    tool_calls = @($toolCall)
}
$continuationBody = @{
    model = "gpt-4o"
    messages = @(
        @{ role = "user"; content = "Use the get_weather tool for Hangzhou, then answer." },
        $assistantMessage,
        @{ role = "tool"; tool_call_id = $toolCall.id; name = "get_weather"; content = "{`"city`":`"Hangzhou`",`"weather`":`"clear`"}" }
    )
    tools = @(
        @{
            type = "function"
            function = @{
                name = "get_weather"
                description = "Get current weather for a city."
                parameters = @{
                    type = "object"
                    properties = @{ city = @{ type = "string" } }
                    required = @("city")
                }
            }
        }
    )
} | ConvertTo-Json -Depth 20 -Compress
$continuation = Invoke-RestMethod -Method POST -Uri "$BaseUrl/chat/completions" -Headers $headers -Body $continuationBody
if (-not $continuation.choices) {
    throw "Tool continuation response does not look like an OpenAI chat completion."
}
Write-Host "OK: external tool_call and continuation completed"
