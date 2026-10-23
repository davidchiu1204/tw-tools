# 台灣生活查詢站 - 一鍵部署到 GitHub Pages（免費）
# 用法：在這個資料夾按右鍵「用 PowerShell 執行」，或在終端機執行  .\deploy.ps1
param([string]$RepoName = "tw-tools")

$ErrorActionPreference = "Continue"
Set-Location $PSScriptRoot
$env:Path = [Environment]::GetEnvironmentVariable("Path", "Machine") + ";" + [Environment]::GetEnvironmentVariable("Path", "User")

function Need($cmd, $id) {
  if (-not (Get-Command $cmd -ErrorAction SilentlyContinue)) {
    Write-Host "安裝 $cmd ..." -ForegroundColor Yellow
    winget install --id $id -e --silent --accept-package-agreements --accept-source-agreements
    $env:Path = [Environment]::GetEnvironmentVariable("Path", "Machine") + ";" + [Environment]::GetEnvironmentVariable("Path", "User")
  }
}
Need git Git.Git
Need gh GitHub.cli

# 1. 登入 GitHub（只需要第一次）
gh auth status 2>$null | Out-Null
if ($LASTEXITCODE -ne 0) {
  Write-Host "`n=== 步驟 1：登入 GitHub ===" -ForegroundColor Cyan
  Write-Host "沒有帳號的話，瀏覽器打開後可以直接註冊（免費）。"
  gh auth login --hostname github.com --git-protocol https --web --scopes "workflow"
}
gh auth setup-git | Out-Null
$owner = (gh api user --jq .login).Trim()
Write-Host "已登入：$owner" -ForegroundColor Green

# 2. 建立 Git 儲存庫並提交
if (-not (Test-Path .git)) { git init -b main | Out-Null }
if (-not (git config user.name))  { git config user.name $owner }
if (-not (git config user.email)) { git config user.email "$owner@users.noreply.github.com" }
git add -A
git diff --cached --quiet
if ($LASTEXITCODE -ne 0) { git commit -m "台灣生活查詢站" | Out-Null }

# 3. 建立 GitHub repo（公開 repo 才能免費使用 Pages）並推送
gh repo view "$owner/$RepoName" 2>$null | Out-Null
if ($LASTEXITCODE -ne 0) {
  Write-Host "`n=== 步驟 2：建立 GitHub repo $owner/$RepoName ===" -ForegroundColor Cyan
  gh repo create $RepoName --public --description "台灣生活查詢站：發票對獎、今日油價、匯率換算（自動更新）" | Out-Null
}
$remote = "https://github.com/$owner/$RepoName.git"
git remote remove origin 2>$null
git remote add origin $remote

# 4. 開啟 GitHub Pages（使用 GitHub Actions 部署）
Write-Host "`n=== 步驟 3：開啟 GitHub Pages ===" -ForegroundColor Cyan
gh api -X POST "repos/$owner/$RepoName/pages" -f build_type=workflow 2>$null | Out-Null
if ($LASTEXITCODE -ne 0) { gh api -X PUT "repos/$owner/$RepoName/pages" -f build_type=workflow 2>$null | Out-Null }

git push -u origin main

Start-Sleep 5
gh api -X PUT "repos/$owner/$RepoName/pages" -f build_type=workflow 2>$null | Out-Null

if ($RepoName -ieq "$owner.github.io") { $url = "https://$($owner.ToLower()).github.io/" }
else { $url = "https://$($owner.ToLower()).github.io/$RepoName/" }

Write-Host "`n=== 完成！===" -ForegroundColor Green
Write-Host "GitHub Actions 正在第一次建置（約 1～2 分鐘），之後每 4 小時自動更新。"
Write-Host "網站網址：$url" -ForegroundColor Green
Write-Host "建置進度：https://github.com/$owner/$RepoName/actions"
Write-Host "`n下一步：照 README.md 的「賺錢設定」申請 Google AdSense 與 Search Console。"
