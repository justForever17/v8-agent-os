param(
    [string]$SourceDir = "",
    [string]$TargetDir = "",
    [string]$DownloadUrl = "",
    [string]$MetadataPath = ""
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"
$ProgressPreference = "SilentlyContinue"

if ([string]::IsNullOrWhiteSpace($TargetDir)) {
    $TargetDir = Join-Path $HOME ".v8-agent-os\\tools\\silk-v3"
}

$resolvedTarget = (New-Item -ItemType Directory -Force -Path $TargetDir).FullName
$binDir = Join-Path $resolvedTarget "bin"
$vendorDir = Join-Path $resolvedTarget "vendor"
New-Item -ItemType Directory -Force -Path $binDir | Out-Null
New-Item -ItemType Directory -Force -Path $vendorDir | Out-Null
$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path

if ([string]::IsNullOrWhiteSpace($MetadataPath)) {
    $MetadataPath = Join-Path $scriptDir "silk_toolchain_release.json"
}

function Remove-StaleArtifacts {
    @(
        (Join-Path $binDir "silk_v3_encoder.cmd"),
        (Join-Path $binDir "silk_v3_encoder.bat"),
        (Join-Path $binDir "silk_v3_encoder.py"),
        (Join-Path $binDir "silk_v3_encoder.exe"),
        (Join-Path $binDir "silk_v3_decoder.cmd"),
        (Join-Path $binDir "silk_v3_decoder.bat"),
        (Join-Path $binDir "silk_v3_decoder.py"),
        (Join-Path $binDir "silk_v3_decoder.exe")
    ) | ForEach-Object {
        if (Test-Path $_) {
            Remove-Item -Force $_
        }
    }
    if (Test-Path $vendorDir) {
        Remove-Item -Recurse -Force $vendorDir
    }
    New-Item -ItemType Directory -Force -Path $vendorDir | Out-Null
}

function Write-ToolchainManifest {
    param(
        [string]$Version,
        [string]$Source,
        [string]$Platform,
        [string]$PackageName,
        [string]$PackageVersion,
        [string]$Status,
        [string]$LastError = ""
    )

    $encoderExpected = @(
        (Join-Path $binDir "silk_v3_encoder.cmd"),
        (Join-Path $binDir "silk_v3_encoder.bat"),
        (Join-Path $binDir "silk_v3_encoder.py"),
        (Join-Path $binDir "silk_v3_encoder.exe")
    )
    $decoderExpected = @(
        (Join-Path $binDir "silk_v3_decoder.cmd"),
        (Join-Path $binDir "silk_v3_decoder.bat"),
        (Join-Path $binDir "silk_v3_decoder.py"),
        (Join-Path $binDir "silk_v3_decoder.exe")
    )
    $encoderPresent = @($encoderExpected | Where-Object { Test-Path $_ })
    $decoderPresent = @($decoderExpected | Where-Object { Test-Path $_ })
    $manifestPath = Join-Path $resolvedTarget "toolchain.json"
    $manifest = [ordered]@{
        version = if ($Version) { $Version } else { "unknown" }
        installedAt = (Get-Date).ToString("o")
        canonicalToolRoot = $resolvedTarget
        entrypoints = @($encoderPresent + $decoderPresent)
        encoderEntrypoints = @($encoderPresent)
        decoderEntrypoints = @($decoderPresent)
        source = $Source
        platform = $Platform
        packageName = $PackageName
        packageVersion = $PackageVersion
        status = if ($Status) { $Status } else { "unknown" }
        lastError = if ($LastError) { $LastError } else { $null }
    }
    $manifest | ConvertTo-Json -Depth 4 | Set-Content -Path $manifestPath -Encoding UTF8
    return @{
        ManifestPath = $manifestPath
        EncoderPresent = $encoderPresent
        DecoderPresent = $decoderPresent
    }
}

function New-EncoderWrapper {
    param(
        [string]$VendorRoot
    )

    $encoderScript = Join-Path $binDir "silk_v3_encoder.py"
@'
from __future__ import annotations

import argparse
import subprocess
import sys
import tempfile
import wave
from pathlib import Path


def _resolve_pcm_input(input_path: Path) -> Path:
    if input_path.suffix.lower() != ".wav":
        return input_path

    with wave.open(str(input_path), "rb") as wav_file:
        channels = wav_file.getnchannels()
        sample_width = wav_file.getsampwidth()
        if channels != 1 or sample_width != 2:
            raise RuntimeError(
                f"Unsupported WAV layout for Silk encoder: channels={channels}, sample_width={sample_width}"
            )
        frames = wav_file.readframes(wav_file.getnframes())

    temp_dir = Path(tempfile.mkdtemp(prefix="v8chat-silk-wrap-"))
    pcm_path = temp_dir / "input.pcm"
    pcm_path.write_bytes(frames)
    return pcm_path


def main() -> int:
    parser = argparse.ArgumentParser(description="V8Chat Silk V3 encoder bridge")
    parser.add_argument("--input", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--sample-rate", required=True, type=int)
    parser.add_argument("--bitrate", required=True, type=int)
    args = parser.parse_args()

    script_dir = Path(__file__).resolve().parent
    tool_root = script_dir.parent
    vendor_root = tool_root / "vendor" / "prebuilt"
    encoder = vendor_root / "silk_v3_encoder.exe"
    if not encoder.exists():
        print(f"silk_v3_encoder.exe not found under {vendor_root}", file=sys.stderr)
        return 2

    temp_pcm: Path | None = None
    try:
        input_path = Path(args.input).expanduser().resolve()
        pcm_path = _resolve_pcm_input(input_path)
        if pcm_path != input_path:
            temp_pcm = pcm_path
        command = [
            str(encoder),
            str(pcm_path),
            str(Path(args.output).expanduser().resolve()),
            "-Fs_API",
            str(args.sample_rate),
            "-rate",
            str(args.bitrate),
            "-tencent",
            "-quiet",
        ]
        result = subprocess.run(command, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True, check=False)
        if result.returncode != 0:
            print(str(result.stderr or "").strip() or f"silk_v3_encoder exited with code {result.returncode}", file=sys.stderr)
            return result.returncode or 1
        return 0
    except Exception as exc:
        print(str(exc).strip() or exc.__class__.__name__, file=sys.stderr)
        return 1
    finally:
        if temp_pcm is not None:
            try:
                temp_pcm.unlink(missing_ok=True)
                temp_pcm.parent.rmdir()
            except Exception:
                pass


if __name__ == "__main__":
    raise SystemExit(main())
'@ | Set-Content -Path $encoderScript -Encoding UTF8
}

function New-DecoderWrapper {
    $decoderScript = Join-Path $binDir "silk_v3_decoder.py"
@'
from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser(description="V8Chat Silk V3 decoder bridge")
    parser.add_argument("--input", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--sample-rate", required=False, type=int, default=24000)
    args = parser.parse_args()

    script_dir = Path(__file__).resolve().parent
    tool_root = script_dir.parent
    vendor_root = tool_root / "vendor" / "prebuilt"
    decoder = vendor_root / "silk_v3_decoder.exe"
    if not decoder.exists():
        print(f"silk_v3_decoder.exe not found under {vendor_root}", file=sys.stderr)
        return 2

    command = [
        str(decoder),
        str(Path(args.input).expanduser().resolve()),
        str(Path(args.output).expanduser().resolve()),
        "-Fs_API",
        str(args.sample_rate),
        "-quiet",
    ]
    result = subprocess.run(command, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True, check=False)
    if result.returncode != 0:
        print(str(result.stderr or "").strip() or f"silk_v3_decoder exited with code {result.returncode}", file=sys.stderr)
        return result.returncode or 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
'@ | Set-Content -Path $decoderScript -Encoding UTF8
}

function Install-NpmContractToolchain {
    param(
        [string]$PackageName,
        [string]$PackageVersion
    )

    Remove-StaleArtifacts
    $vendorRoot = Join-Path $resolvedTarget "vendor\\wx-voice"
    New-Item -ItemType Directory -Force -Path $vendorRoot | Out-Null
    $packageSpec = if ($PackageVersion) { "$PackageName@$PackageVersion" } else { $PackageName }
    $npmCommand = Get-Command npm.cmd -ErrorAction SilentlyContinue
    if (-not $npmCommand) {
        $npmCommand = Get-Command npm -ErrorAction SilentlyContinue
    }
    if (-not $npmCommand) {
        throw "npm not found in PATH. The npm Silk fallback requires npm to install wx-voice."
    }
    Push-Location $vendorRoot
    try {
        & $npmCommand.Source init -y | Out-Null
        & $npmCommand.Source install --no-package-lock --no-audit --no-fund $packageSpec
        $nodeCommand = Get-Command node -ErrorAction SilentlyContinue
        if (-not $nodeCommand) {
            throw "node not found in PATH. Silk fallback installation requires Node.js."
        }
        $cliPath = Join-Path $vendorRoot "node_modules\\wx-voice\\bin.js"
        if (-not (Test-Path $cliPath)) {
            throw "wx-voice CLI not found after install: $cliPath"
        }
        $compileOutput = (& $nodeCommand.Source $cliPath compile 2>&1 | Out-String)
        $silkEncoder = Join-Path $vendorRoot "node_modules\\wx-voice\\silk\\encoder"
        $silkDecoder = Join-Path $vendorRoot "node_modules\\wx-voice\\silk\\decoder"
        if ($LASTEXITCODE -ne 0 -or -not (Test-Path $silkEncoder) -or -not (Test-Path $silkDecoder)) {
            $message = ($compileOutput -split "`r?`n" | Where-Object { -not [string]::IsNullOrWhiteSpace($_) }) -join " | "
            if ([string]::IsNullOrWhiteSpace($message)) {
                $message = "wx-voice compile did not produce usable Silk encoder/decoder binaries."
            }
            throw "wx-voice compile failed. $message Install the required build tools or provide a prepared Silk toolchain."
        }
    }
    finally {
        Pop-Location
    }
    $encoderScript = Join-Path $binDir "silk_v3_encoder.py"
@'
from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser(description="V8Chat Silk V3 encoder bridge")
    parser.add_argument("--input", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--sample-rate", required=True, type=int)
    parser.add_argument("--bitrate", required=True, type=int)
    args = parser.parse_args()

    script_dir = Path(__file__).resolve().parent
    tool_root = script_dir.parent
    vendor_root = tool_root / "vendor" / "wx-voice"
    cli = vendor_root / "node_modules" / "wx-voice" / "bin.js"
    if not cli.exists():
        print(f"wx-voice CLI not found under {vendor_root}", file=sys.stderr)
        return 2

    node = shutil.which("node")
    if not node:
        print("Node.js not found in PATH.", file=sys.stderr)
        return 3

    command = [
        node,
        str(cli),
        "encode",
        "-i",
        args.input,
        "-o",
        args.output,
        "-f",
        "silk",
        "--bitrate",
        str(args.bitrate),
        "--frequency",
        str(args.sample_rate),
        "--channels",
        "1",
    ]
    result = subprocess.run(command, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True, check=False)
    if result.returncode != 0:
        print(str(result.stderr or "").strip() or f"wx-voice exited with code {result.returncode}", file=sys.stderr)
        return result.returncode or 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
'@ | Set-Content -Path $encoderScript -Encoding UTF8
}

function Install-PrebuiltFileSetToolchain {
    param(
        [string]$EncoderUrl,
        [string]$DecoderUrl
    )

    if ([string]::IsNullOrWhiteSpace($EncoderUrl)) {
        throw "Missing encoderUrl for prebuilt Silk toolchain."
    }
    if ([string]::IsNullOrWhiteSpace($DecoderUrl)) {
        throw "Missing decoderUrl for prebuilt Silk toolchain."
    }

    Remove-StaleArtifacts
    $vendorRoot = Join-Path $resolvedTarget "vendor\\prebuilt"
    New-Item -ItemType Directory -Force -Path $vendorRoot | Out-Null

    Invoke-WebRequest -UseBasicParsing -Uri $EncoderUrl -OutFile (Join-Path $vendorRoot "silk_v3_encoder.exe")
    Invoke-WebRequest -UseBasicParsing -Uri $DecoderUrl -OutFile (Join-Path $vendorRoot "silk_v3_decoder.exe")

    if (-not (Test-Path (Join-Path $vendorRoot "silk_v3_encoder.exe"))) {
        throw "Prebuilt silk_v3_encoder.exe was not downloaded successfully."
    }
    if (-not (Test-Path (Join-Path $vendorRoot "silk_v3_decoder.exe"))) {
        throw "Prebuilt silk_v3_decoder.exe was not downloaded successfully."
    }

    New-EncoderWrapper -VendorRoot $vendorRoot
    New-DecoderWrapper
}

function Expand-DownloadedArchive {
    param(
        [string]$Url
    )

    $tempRoot = Join-Path ([System.IO.Path]::GetTempPath()) ("v8chat-silk-" + [System.Guid]::NewGuid().ToString("N"))
    New-Item -ItemType Directory -Force -Path $tempRoot | Out-Null
    $archivePath = Join-Path $tempRoot "toolchain.zip"
    $extractRoot = Join-Path $tempRoot "extract"
    Invoke-WebRequest -UseBasicParsing -Uri $Url -OutFile $archivePath
    Expand-Archive -LiteralPath $archivePath -DestinationPath $extractRoot -Force
    $children = @(Get-ChildItem -Path $extractRoot)
    if ($children.Count -eq 1 -and $children[0].PSIsContainer) {
        return $children[0].FullName
    }
    return $extractRoot
}

function Copy-PreparedToolchain {
    param(
        [string]$PreparedSource
    )

    Remove-StaleArtifacts
    $resolvedSource = (Resolve-Path $PreparedSource).Path
    Copy-Item -Path (Join-Path $resolvedSource "*") -Destination $resolvedTarget -Recurse -Force
}

if (-not [string]::IsNullOrWhiteSpace($SourceDir)) {
    Copy-PreparedToolchain -PreparedSource $SourceDir
    $result = Write-ToolchainManifest -Version "external" -Source "source_dir" -Platform "windows-x64" -PackageName "" -PackageVersion "" -Status "ready"
} elseif (-not [string]::IsNullOrWhiteSpace($DownloadUrl)) {
    $expanded = Expand-DownloadedArchive -Url $DownloadUrl
    Copy-PreparedToolchain -PreparedSource $expanded
    $result = Write-ToolchainManifest -Version "download" -Source "download_url" -Platform "windows-x64" -PackageName "" -PackageVersion "" -Status "ready"
} else {
    if (-not (Test-Path $MetadataPath)) {
        throw "Silk release metadata not found: $MetadataPath"
    }
    $metadata = Get-Content -Path $MetadataPath -Raw | ConvertFrom-Json
    $platformKey = "windows-x64"
    $platformConfig = $metadata.platforms.$platformKey
    if (-not $platformConfig) {
        throw "Silk release metadata does not define platform '$platformKey'."
    }
    $sourceType = [string]$platformConfig.sourceType
    try {
        if ($sourceType -eq "prebuilt_file_set") {
            Install-PrebuiltFileSetToolchain `
                -EncoderUrl ([string]$platformConfig.encoderUrl) `
                -DecoderUrl ([string]$platformConfig.decoderUrl)
            $result = Write-ToolchainManifest `
                -Version ([string]$metadata.version) `
                -Source ("v8-managed:prebuilt:{0}@{1}" -f ([string]$platformConfig.sourceName), ([string]$metadata.version)) `
                -Platform $platformKey `
                -PackageName ([string]$platformConfig.sourceName) `
                -PackageVersion ([string]$metadata.version) `
                -Status "ready"
        } elseif ($sourceType -eq "npm_contract") {
            $packageName = [string]$platformConfig.packageName
            $packageVersion = [string]$platformConfig.packageVersion
            Install-NpmContractToolchain -PackageName $packageName -PackageVersion $packageVersion
            $result = Write-ToolchainManifest `
                -Version ([string]$metadata.version) `
                -Source ("v8-managed:npm:{0}@{1}" -f $packageName, $packageVersion) `
                -Platform $platformKey `
                -PackageName $packageName `
                -PackageVersion $packageVersion `
                -Status "ready"
        } else {
            throw "Unsupported Silk sourceType: $sourceType"
        }
    } catch {
        $packageName = ""
        $packageVersion = ""
        $source = "v8-managed:unknown"
        if ($sourceType -eq "prebuilt_file_set") {
            $packageName = [string]$platformConfig.sourceName
            $packageVersion = [string]$metadata.version
            $source = "v8-managed:prebuilt:{0}@{1}" -f $packageName, $packageVersion
        } elseif ($sourceType -eq "npm_contract") {
            $packageName = [string]$platformConfig.packageName
            $packageVersion = [string]$platformConfig.packageVersion
            $source = "v8-managed:npm:{0}@{1}" -f $packageName, $packageVersion
        }
        $result = Write-ToolchainManifest `
            -Version ([string]$metadata.version) `
            -Source $source `
            -Platform $platformKey `
            -PackageName $packageName `
            -PackageVersion $packageVersion `
            -Status "error" `
            -LastError $_.Exception.Message
        throw
    }
}

$encoderPresent = @($result.EncoderPresent)
$decoderPresent = @($result.DecoderPresent)
$manifestPath = [string]$result.ManifestPath

Write-Host "[v8chat] Silk toolchain target: $resolvedTarget"
if ($encoderPresent.Count -gt 0) {
    Write-Host "[v8chat] Detected encoder entrypoint:"
    $encoderPresent | ForEach-Object { Write-Host "  - $_" }
} else {
    Write-Warning "[v8chat] No encoder entrypoint detected yet."
}
if ($decoderPresent.Count -gt 0) {
    Write-Host "[v8chat] Detected decoder entrypoint:"
    $decoderPresent | ForEach-Object { Write-Host "  - $_" }
}
Write-Host "[v8chat] Toolchain manifest: $manifestPath"

Write-Host ""
Write-Host "[v8chat] Export this env var for Engine if you override the canonical root:"
Write-Host "  `$env:V8_AGENT_OS_SILK_TOOL_ROOT = `"$resolvedTarget`""
