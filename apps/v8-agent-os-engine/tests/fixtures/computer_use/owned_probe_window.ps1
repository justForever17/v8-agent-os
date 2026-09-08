param([Parameter(Mandatory=$true)][string]$ProbeTitle, [Parameter(Mandatory=$true)][string]$ProbeDirectory)

$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName System.Windows.Forms
Add-Type -AssemblyName System.Drawing
[System.Windows.Forms.Application]::EnableVisualStyles()

$probeForm = New-Object System.Windows.Forms.Form
$probeForm.Text = $ProbeTitle
$probeForm.Name = 'OwnedProbe'
$probeForm.StartPosition = 'Manual'
$probeForm.Location = New-Object System.Drawing.Point(80, 80)
$probeForm.Size = New-Object System.Drawing.Size(900, 500)
$probeForm.TopMost = $true
$probeForm.BackColor = [System.Drawing.Color]::FromArgb(24, 58, 82)

$probeLabel = New-Object System.Windows.Forms.Label
$probeLabel.Text = 'V8OS owned GUI acceptance - synthetic text only'
$probeLabel.ForeColor = [System.Drawing.Color]::White
$probeLabel.Location = New-Object System.Drawing.Point(40, 30)
$probeLabel.Size = New-Object System.Drawing.Size(760, 40)
$probeForm.Controls.Add($probeLabel)

$probeInput = New-Object System.Windows.Forms.TextBox
$probeInput.Name = 'OwnedInput'
$probeInput.Location = New-Object System.Drawing.Point(40, 110)
$probeInput.Size = New-Object System.Drawing.Size(620, 32)
$probeForm.Controls.Add($probeInput)

$probeSubmit = New-Object System.Windows.Forms.Button
$probeSubmit.Name = 'OwnedSubmit'
$probeSubmit.Text = 'Submit owned test'
$probeSubmit.Location = New-Object System.Drawing.Point(40, 170)
$probeSubmit.Size = New-Object System.Drawing.Size(200, 45)
$probeSubmit.Add_Click({
    $probeLabel.Text = 'Submitted: ' + $probeInput.Text
    @{ pid = $PID; submittedText = $probeInput.Text } | ConvertTo-Json -Compress | Set-Content -LiteralPath (Join-Path $ProbeDirectory 'submitted.json') -Encoding UTF8
})
$probeForm.Controls.Add($probeSubmit)

$probeDialog = New-Object System.Windows.Forms.Form
$probeDialog.Text = $ProbeTitle + '-dialog'
$probeDialog.Name = 'OwnedSmallDialog'
$probeDialog.FormBorderStyle = 'FixedDialog'
$probeDialog.StartPosition = 'Manual'
$probeDialog.Location = New-Object System.Drawing.Point(1020, 100)
$probeDialog.Size = New-Object System.Drawing.Size(220, 130)
$probeDialog.BackColor = [System.Drawing.Color]::FromArgb(34, 89, 54)
$probeDialog.TopMost = $true
$dialogLabel = New-Object System.Windows.Forms.Label
$dialogLabel.Text = 'Explicit small dialog'
$dialogLabel.Location = New-Object System.Drawing.Point(10, 20)
$dialogLabel.AutoSize = $true
$probeDialog.Controls.Add($dialogLabel)

$probeTimer = New-Object System.Windows.Forms.Timer
$probeTimer.Interval = 100
$probeTimer.Add_Tick({
    if (Test-Path -LiteralPath (Join-Path $ProbeDirectory 'close.marker')) {
        $probeTimer.Stop()
        $probeDialog.Close()
        $probeForm.Close()
    }
})
$probeForm.Add_Shown({
    $probeDialog.Show($probeForm)
    $probeForm.Activate()
    $probeInput.Focus()
    @{ pid = $PID; title = $ProbeTitle; mainHandle = $probeForm.Handle.ToInt64(); dialogHandle = $probeDialog.Handle.ToInt64(); inputHandle = $probeInput.Handle.ToInt64(); submitHandle = $probeSubmit.Handle.ToInt64(); screens = @([System.Windows.Forms.Screen]::AllScreens | ForEach-Object { @{ width = $_.Bounds.Width; height = $_.Bounds.Height; left = $_.Bounds.Left; top = $_.Bounds.Top; primary = $_.Primary } }) } | ConvertTo-Json -Depth 5 | Set-Content -LiteralPath (Join-Path $ProbeDirectory 'ready.json') -Encoding UTF8
    $probeTimer.Start()
})
try {
    [System.Windows.Forms.Application]::Run($probeForm)
} finally {
    $probeTimer.Dispose()
    $probeDialog.Dispose()
    $probeForm.Dispose()
}
