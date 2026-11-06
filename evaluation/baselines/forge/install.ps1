# Foundry 바이너리 설치 (evaluation/baselines/forge/bin). 버전은 평가에 쓴 것으로 고정.
# v1.9.0 = 2026-11-02 릴리스, commit 6056c5845d34205a83cf7108042adb137325c9df.
#   이 버전에서 내장 디버거가 제거되고 SolDB 0.4.5 로 교체됐다(PR #17429).
#   --debug-layout 제거, --dump 는 JSON 파일 → SolDB bundle 디렉터리.
#   이전 기준이던 v1.8.6(2026-10-21, commit 6bad0a66)의 측정치는 모두 폐기했다.
# 커밋하지 않는다(../.gitignore).
$ErrorActionPreference = "Stop"
$ver = "v1.9.0"
$dst = Join-Path $PSScriptRoot "bin"
$zip = Join-Path $env:TEMP "foundry_$ver.zip"
New-Item -ItemType Directory -Force $dst | Out-Null
Invoke-WebRequest "https://github.com/foundry-rs/foundry/releases/download/$ver/foundry_${ver}_win32_amd64.zip" -OutFile $zip
Expand-Archive $zip -DestinationPath $dst -Force
Remove-Item $zip
& (Join-Path $dst "forge.exe") --version
