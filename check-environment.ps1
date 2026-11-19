$ErrorActionPreference = "Continue"

$projectRoot = $PSScriptRoot
$results = [System.Collections.Generic.List[object]]::new()

function Add-Check {
    param(
        [string]$Name,
        [string]$Command,
        [string]$Level,
        [string]$Suggestion
    )

    $item = Get-Command $Command -ErrorAction SilentlyContinue
    if ($item) {
        try {
            $version = (& $Command --version 2>&1 | Select-Object -First 1).ToString()
        }
        catch {
            $version = "Installed; version command failed"
        }

        $results.Add([PSCustomObject]@{
            Item = $Name
            Status = "OK"
            Version = $version
            Suggestion = ""
        })
        return
    }

    $status = "Optional; not installed"
    if ($Level -eq "Required") {
        $status = "MISSING"
    }

    $results.Add([PSCustomObject]@{
        Item = $Name
        Status = $status
        Version = ""
        Suggestion = $Suggestion
    })
}

Add-Check "Python" "python" "Required" "Install Python 3.12 or newer"
Add-Check "VS Code" "code" "Required" "Install Visual Studio Code"
Add-Check "Git" "git" "Required" "Install Git for Windows"
Add-Check "uv" "uv" "Recommended" "Install uv for Python project management"
Add-Check "Node.js" "node" "Recommended" "Used by visual workflow platforms"
Add-Check "nvidia-smi" "nvidia-smi" "Optional" "Install the NVIDIA driver for local GPU models"
Add-Check "Docker" "docker" "Advanced" "Install Docker Desktop for containers"

$wslCommand = Get-Command "wsl" -ErrorAction SilentlyContinue
$wslStatus = "Optional; not installed"
$wslVersion = ""
if ($wslCommand) {
    & wsl -l -q *> $null
    if ($LASTEXITCODE -eq 0) {
        $wslStatus = "OK"
        $wslVersion = "Linux distribution installed"
    }
    else {
        $wslStatus = "Command exists; no Linux distribution"
    }
}
$results.Add([PSCustomObject]@{
    Item = "WSL"
    Status = $wslStatus
    Version = $wslVersion
    Suggestion = "Run wsl --install -d Ubuntu in an Administrator PowerShell"
})

$gitName = git config --global --get user.name
$gitEmail = git config --global --get user.email
$gitNameStatus = "OK"
$gitEmailStatus = "OK"
if (-not $gitName) {
    $gitNameStatus = "MISSING"
}
if (-not $gitEmail) {
    $gitEmailStatus = "MISSING"
}

$results.Add([PSCustomObject]@{
    Item = "Git user name"
    Status = $gitNameStatus
    Version = $gitName
    Suggestion = 'Run: git config --global user.name "Your Name"'
})
$results.Add([PSCustomObject]@{
    Item = "Git email"
    Status = $gitEmailStatus
    Version = $gitEmail
    Suggestion = 'Run: git config --global user.email "you@example.com"'
})

$venvPython = Join-Path $projectRoot ".venv\Scripts\python.exe"
$venvStatus = "MISSING"
if (Test-Path -LiteralPath $venvPython) {
    $venvStatus = "OK"
}
$results.Add([PSCustomObject]@{
    Item = "Main project venv"
    Status = $venvStatus
    Version = $venvPython
    Suggestion = "Run: python -m venv .venv"
})

$envFile = Join-Path $projectRoot ".env"
$apiKeyConfigured = $false
$modelName = ""
if (Test-Path -LiteralPath $envFile) {
    $envLines = Get-Content -LiteralPath $envFile
    $apiKeyLine = $envLines | Where-Object { $_ -match "^OPENAI_API_KEY=" } | Select-Object -First 1
    $modelLine = $envLines | Where-Object { $_ -match "^OPENAI_MODEL=" } | Select-Object -First 1
    if ($apiKeyLine) {
        $apiKeyConfigured = -not $apiKeyLine.EndsWith("=")
    }
    if ($modelLine) {
        $modelName = ($modelLine -split "=", 2)[1].Trim()
    }
}

$apiKeyStatus = "MISSING"
$apiKeyVersion = ""
if ($apiKeyConfigured) {
    $apiKeyStatus = "OK"
    $apiKeyVersion = "Configured; value hidden"
}
$results.Add([PSCustomObject]@{
    Item = "Model API Key"
    Status = $apiKeyStatus
    Version = $apiKeyVersion
    Suggestion = "Fill OPENAI_API_KEY in the .env file"
})

$modelStatus = "MISSING"
if ($modelName) {
    $modelStatus = "OK"
}
$results.Add([PSCustomObject]@{
    Item = "Model name"
    Status = $modelStatus
    Version = $modelName
    Suggestion = "Set a model name supported by your API provider"
})

$results | Format-Table -AutoSize -Wrap

Write-Host ""
Write-Host "Minimum for Phase 1: Python, VS Code, Git."
Write-Host "Recommended next: uv."
Write-Host "WSL and Docker are optional until you start deployment projects."
