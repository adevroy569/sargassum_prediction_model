# ---------------------------------------------------------------------------
# One-time repo fix for the Sargassum site.
#
# Run this from the repo folder, then commit and push with GitHub Desktop:
#
#     cd C:\Users\Asus\Desktop\Claudeworkspace\sargassum_prediction_model
#     powershell -ExecutionPolicy Bypass -File .\fix-workflows.ps1
#
# What it does:
#   1. Creates .github\workflows\ if it is missing.
#   2. Moves update.yml, backfill.yml, retrain.yml and test.yml into it.
#      GitHub only runs workflows from .github/workflows/, which is why the
#      forecast job has never fired and site/data/ has never been generated.
#   3. Rewrites .github\workflows\static.yml so the Pages deploy also runs
#      after the forecast job commits new data.
#   4. Removes the now-empty github-workflows\ folder.
#
# Claude cannot write into .github/workflows/ over the desktop bridge - it is
# a protected path, since files there run CI on your account. Hence this script.
# ---------------------------------------------------------------------------

$ErrorActionPreference = 'Stop'
$repo = $PSScriptRoot
if (-not $repo) { $repo = (Get-Location).Path }

Write-Host "Repo: $repo" -ForegroundColor Cyan

$wf  = Join-Path $repo '.github\workflows'
$old = Join-Path $repo 'github-workflows'

# --- 1. destination -------------------------------------------------------
if (-not (Test-Path $wf)) {
    New-Item -ItemType Directory -Path $wf -Force | Out-Null
    Write-Host "created .github\workflows\"
}

# --- 2. move the four pipeline workflows ----------------------------------
$moved = 0
foreach ($name in 'update.yml', 'backfill.yml', 'retrain.yml', 'test.yml') {
    $src = Join-Path $old $name
    if (Test-Path $src) {
        Move-Item -Path $src -Destination (Join-Path $wf $name) -Force
        Write-Host "moved  $name  ->  .github\workflows\"
        $moved++
    } elseif (Test-Path (Join-Path $wf $name)) {
        Write-Host "already in place: $name"
    } else {
        Write-Warning "not found anywhere: $name"
    }
}

# --- 3. rewrite the Pages deploy workflow ---------------------------------
$static = @'
# Simple workflow for deploying static content to GitHub Pages
name: Deploy static content to Pages

on:
  # Runs on pushes targeting the default branch
  push:
    branches: ["main"]

  # Runs after the forecast job finishes and commits new data.
  # This is not optional: a commit pushed by a workflow using the built-in
  # GITHUB_TOKEN does NOT start other workflows, so without this trigger the
  # site would keep serving whatever data you last pushed by hand.
  workflow_run:
    workflows: ["Sargassum forecast update"]
    types: [completed]

  # Allows you to run this workflow manually from the Actions tab
  workflow_dispatch:

# Sets permissions of the GITHUB_TOKEN to allow deployment to GitHub Pages
permissions:
  contents: read
  pages: write
  id-token: write

# Allow only one concurrent deployment, skipping runs queued between the run in-progress and latest queued.
# However, do NOT cancel in-progress runs as we want to allow these production deployments to complete.
concurrency:
  group: "pages"
  cancel-in-progress: false

jobs:
  # Single deploy job since we're just deploying
  deploy:
    environment:
      name: github-pages
      url: ${{ steps.deployment.outputs.page_url }}
    runs-on: ubuntu-latest
    steps:
      - name: Checkout
        uses: actions/checkout@v4
        with:
          # On a workflow_run event the default checkout ref is the commit that
          # STARTED the forecast job, which is one commit older than the data it
          # just committed. Always take the current tip of main.
          ref: main
      - name: Setup Pages
        uses: actions/configure-pages@v5
      - name: Upload artifact
        uses: actions/upload-pages-artifact@v3
        with:
          # Upload only the website folder
          path: './site'
      - name: Deploy to GitHub Pages
        id: deployment
        uses: actions/deploy-pages@v5
'@

# UTF-8 without BOM, LF line endings - a BOM makes the Actions YAML parser choke.
$static = $static -replace "`r`n", "`n"
[System.IO.File]::WriteAllText(
    (Join-Path $wf 'static.yml'),
    $static,
    (New-Object System.Text.UTF8Encoding($false))
)
Write-Host "rewrote .github\workflows\static.yml"

# --- 4. clean up ----------------------------------------------------------
if (Test-Path $old) {
    Remove-Item -Path $old -Recurse -Force
    Write-Host "removed github-workflows\"
}

Write-Host ""
Write-Host "Now in .github\workflows\:" -ForegroundColor Cyan
Get-ChildItem $wf | Select-Object -ExpandProperty Name | ForEach-Object { Write-Host "  $_" }
Write-Host ""
Write-Host "Next: commit and push in GitHub Desktop, then do the two steps in FIX-THE-BLANK-SITE.md" -ForegroundColor Yellow
