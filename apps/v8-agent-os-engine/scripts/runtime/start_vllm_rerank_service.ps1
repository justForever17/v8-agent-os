param(
    [string]$ContainerName = "v8chat-vllm-rerank",
    [string]$ModelId = "Alibaba-NLP/gte-multilingual-reranker-base",
    [string]$BindHost = "127.0.0.1",
    [int]$Port = 8012,
    [string]$ApiKey = "local-vllm-rerank",
    [string]$ProviderId = "local_vllm_rerank",
    [string]$ProviderName = "Local vLLM Rerank",
    [ValidateSet("auto", "cpu", "cuda")]
    [string]$TargetDevice = "auto",
    [string]$Image = "vllm/vllm-openai:latest",
    [string]$CpuImage = "vllm/vllm-openai-cpu:latest-x86_64",
    [int]$WaitSeconds = 480,
    [switch]$BindGlobalReranker,
    [switch]$BindGlobalRerankerIfEmpty,
    [string]$PythonPath = ""
)

$ErrorActionPreference = "Stop"

function Start-DockerDesktopIfNeeded {
    $dockerDesktop = "C:\Program Files\Docker\Docker\Docker Desktop.exe"
    if (-not (Test-Path $dockerDesktop)) {
        return
    }
    if (Get-Process -Name "Docker Desktop" -ErrorAction SilentlyContinue) {
        return
    }
    Write-Host "[v8chat] Docker Desktop is not running. Launching..." -ForegroundColor Yellow
    Start-Process -FilePath $dockerDesktop | Out-Null
}

function Wait-DockerDaemon {
    param([int]$TimeoutSeconds = 120)
    $deadline = (Get-Date).AddSeconds($TimeoutSeconds)
    do {
        try {
            docker version | Out-Null
            return $true
        } catch {
            Start-Sleep -Seconds 3
        }
    } while ((Get-Date) -lt $deadline)
    return $false
}

function Resolve-Python {
    param([string]$ExplicitPath = "")
    if (-not [string]::IsNullOrWhiteSpace($ExplicitPath)) {
        if (-not (Test-Path $ExplicitPath)) {
            throw "Python executable not found: $ExplicitPath"
        }
        return $ExplicitPath
    }
    $candidates = @(
        (Join-Path $PSScriptRoot "..\..\.venv\Scripts\python.exe"),
        (Join-Path $PSScriptRoot "..\..\venv\Scripts\python.exe")
    )
    foreach ($candidate in $candidates) {
        if (Test-Path $candidate) { return $candidate }
    }
    throw "Canonical engine interpreter not found. Create v8-agent-os-engine/.venv or pass -PythonPath explicitly."
}

function Test-NvidiaRuntime {
    if (-not (Get-Command nvidia-smi -ErrorAction SilentlyContinue)) {
        return $false
    }
    try {
        nvidia-smi -L | Out-Null
        return $true
    } catch {
        return $false
    }
}

function Invoke-RerankHealthCheck {
    param(
        [string]$Endpoint,
        [string]$Model,
        [string]$BearerToken,
        [int]$TimeoutSeconds = 240
    )
    $deadline = (Get-Date).AddSeconds($TimeoutSeconds)
    $body = @{
        model = $Model
        query = "connection test"
        documents = @("connection test", "fallback document")
        top_n = 1
        return_documents = $true
    } | ConvertTo-Json -Depth 4

    do {
        try {
            $response = Invoke-RestMethod `
                -Method Post `
                -Uri $Endpoint `
                -Headers @{ Authorization = "Bearer $BearerToken" } `
                -ContentType "application/json" `
                -Body $body `
                -TimeoutSec 30
            if ($response.results) {
                return $true
            }
        } catch {
            Start-Sleep -Seconds 4
        }
    } while ((Get-Date) -lt $deadline)
    return $false
}

if (-not (Get-Command docker -ErrorAction SilentlyContinue)) {
    throw "docker was not found. Please install Docker Desktop first."
}

Start-DockerDesktopIfNeeded
if (-not (Wait-DockerDaemon -TimeoutSeconds 150)) {
    throw "docker daemon is not reachable. Please make sure Docker Desktop is fully started."
}

$pythonExe = Resolve-Python -ExplicitPath $PythonPath
$cacheDir = Join-Path ${env:USERPROFILE} '.cache\huggingface'
New-Item -ItemType Directory -Force -Path $cacheDir | Out-Null

$containerFilter = "name=^${ContainerName}$"
$existing = docker ps -a --filter $containerFilter --format "{{.ID}}"
if ($existing) {
    docker rm -f $ContainerName | Out-Null
}

$resolvedDevice = $TargetDevice
if ($resolvedDevice -eq "auto") {
    $resolvedDevice = if (Test-NvidiaRuntime) { "cuda" } else { "cpu" }
}

$resolvedImage = if ($resolvedDevice -eq "cuda") { $Image } else { $CpuImage }
$dockerArgs = @("run", "-d", "--name", $ContainerName, "-p", ("{0}:{1}:8000" -f $BindHost, $Port), "-v", ("{0}:/root/.cache/huggingface" -f $cacheDir))

if ($resolvedDevice -eq "cuda") {
    $dockerArgs += @("--gpus", "all")
} else {
    $dockerArgs += @("--security-opt", "seccomp=unconfined", "--cap-add", "SYS_NICE", "--shm-size", "4g", "-e", "VLLM_CPU_KVCACHE_SPACE=2")
}

$dockerArgs += @(
    $resolvedImage,
    "--model", $ModelId,
    "--served-model-name", $ModelId,
    "--task", "score",
    "--api-key", $ApiKey,
    "--device", $resolvedDevice
)

if ($resolvedDevice -eq "cpu") {
    $dockerArgs += @("--dtype", "float")
}

Write-Host ("[v8chat] Starting vLLM rerank service ({0})..." -f $resolvedDevice) -ForegroundColor Cyan
$containerId = docker @dockerArgs
if (-not $containerId) {
    throw "vLLM container failed to start."
}

$registerScript = Join-Path $PSScriptRoot "register_local_vllm_rerank.py"
$registerArgs = @(
    $registerScript,
    "--provider-id", $ProviderId,
    "--provider-name", $ProviderName,
    "--model-id", $ModelId,
    "--base-url", ("http://{0}:{1}/v1" -f $BindHost, $Port),
    "--api-key", $ApiKey
)
if ($BindGlobalReranker) {
    $registerArgs += "--bind-global-reranker"
}
if ($BindGlobalRerankerIfEmpty) {
    $registerArgs += "--bind-global-reranker-if-empty"
}

& $pythonExe @registerArgs

$healthUrl = "http://$BindHost`:$Port/v1/rerank"
if (-not (Invoke-RerankHealthCheck -Endpoint $healthUrl -Model $ModelId -BearerToken $ApiKey -TimeoutSeconds $WaitSeconds)) {
    throw "vLLM rerank health check timed out."
}

Write-Host ("[v8chat] Container ready: {0}" -f $ContainerName) -ForegroundColor Green
Write-Host ("[v8chat] Endpoint: {0}" -f $healthUrl) -ForegroundColor Green
Write-Host "[v8chat] Next: bind the model in Admin -> Model Hub / Memory / Extensions / Desktop Automation." -ForegroundColor Yellow
