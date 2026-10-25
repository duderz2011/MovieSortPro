# Run AFTER creating an empty public repository at https://github.com/new
# Usage: .\Publish-To-GitHub.ps1 -Repository 'YOUR_USERNAME/MovieSortPro'
param(
    [Parameter(Mandatory=$true)]
    [ValidatePattern('^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$')]
    [string] $Repository
)

$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot

if (-not (Get-Command git -ErrorAction SilentlyContinue)) {
    throw 'Git for Windows is not installed. Get it from https://git-scm.com/download/win and reopen PowerShell.'
}

$remote = "https://github.com/$Repository.git"
if (-not (Test-Path -LiteralPath '.git')) {
    git init -b main
    if ($LASTEXITCODE -ne 0) { throw 'git init failed.' }
}

git add .
if ($LASTEXITCODE -ne 0) { throw 'git add failed.' }

git diff --cached --quiet
if ($LASTEXITCODE -ne 0) {
    git commit -m 'Initial open-source release of MovieSort Pro v2.1'
    if ($LASTEXITCODE -ne 0) {
        throw 'git commit failed. Configure git user.name and git user.email, then retry.'
    }
}

git branch -M main
if ($LASTEXITCODE -ne 0) { throw 'Unable to set main branch.' }

$existing = (& git remote get-url origin 2>$null)
if ($LASTEXITCODE -eq 0) {
    if ($existing.Trim() -ne $remote) {
        throw "An origin remote is already set to '$existing'. Refusing to overwrite it."
    }
} else {
    git remote add origin $remote
    if ($LASTEXITCODE -ne 0) { throw 'Could not add GitHub origin remote.' }
}

Write-Host "Uploading to https://github.com/$Repository ..."
git push -u origin main
if ($LASTEXITCODE -ne 0) { throw 'git push failed; check GitHub access and that the repository exists.' }
Write-Host "Published source: https://github.com/$Repository" -ForegroundColor Green
Write-Host 'A Windows build will appear under the Actions tab when the workflow completes.'
