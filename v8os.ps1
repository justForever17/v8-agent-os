$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $MyInvocation.MyCommand.Path
$bin = Join-Path $root "apps/v8-agent-os-cli/bin/v8os.mjs"
if (-not (Get-Command node -ErrorAction SilentlyContinue)) {
  throw "V8OS CLI requires Node.js 20 or newer on PATH."
}
& node $bin @args
exit $LASTEXITCODE
