[CmdletBinding()]
param([string]$Source = (Join-Path $PSScriptRoot '../../../../ZCode'))

$ErrorActionPreference = 'Stop'
$sourcePath = (Resolve-Path -LiteralPath $Source).Path
$revision = 'b95ebaffeab641907b38652f45c1502bd762972d'
$main = Join-Path $sourcePath 'packages/desktop/src/main'
if ((git -C $sourcePath rev-parse HEAD) -ne $revision) {
    throw "Expected ZCode revision $revision."
}
foreach ($file in @('index.ts', 'appARMSBootstrap.ts', 'desktopWindowsOpenFolderContextMenu.ts')) {
    if (!(Select-String -LiteralPath (Join-Path $main $file) -SimpleMatch 'ZCODE_REFERENCE_SKIP_PROTOCOL_REGISTRATION' -Quiet)) {
        throw 'Apply windows-reference.patch before starting the reference instance.'
    }
}

$project = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '../../..')).Path
$samplePath = Join-Path $project ("temp/zcode-reference-" + [guid]::NewGuid().ToString('N'))
$electronPath = Join-Path $samplePath 'electron'
$sessionPath = Join-Path $electronPath 'session'
$runtimeTemp = Join-Path $samplePath 'runtime-temp'
New-Item -ItemType Directory -Path $sessionPath | Out-Null
New-Item -ItemType Directory -Path $runtimeTemp | Out-Null
$env:ZCODE_DATA_BASE_DIR = $samplePath
$env:ZCODE_DESKTOP_APPLICATION_NAME = 'ZCode Reference'
$env:ZCODE_DESKTOP_HOME_DIR = $samplePath
$env:ZCODE_DESKTOP_USER_DATA_DIR = $electronPath
$env:ZCODE_DESKTOP_SESSION_DATA_DIR = $sessionPath
$env:USERPROFILE = $samplePath
$env:TEMP = $runtimeTemp
$env:TMP = $runtimeTemp
$env:ZCODE_HOME = Join-Path $samplePath '.zcode'
$env:ZCODE_REACT_SCAN = '0'
$env:ZCODE_REFERENCE_SKIP_PROTOCOL_REGISTRATION = '1'
$env:ZCODE_ENV = 'test'
$env:ZCODE_BASE_URL = 'http://127.0.0.1:18764'
$env:ZCODE_PRODUCTION_BASE_URL = $env:ZCODE_BASE_URL
$env:ZCODE_TEST_BASE_URL = $env:ZCODE_BASE_URL

$tsx = Join-Path $sourcePath 'node_modules/tsx/dist/cli.mjs'
& node $tsx (Join-Path $PSScriptRoot 'seed-reference.mts') $sourcePath $samplePath
if ($LASTEXITCODE) { throw 'Could not seed the local model.' }

$fixtureFile = Join-Path $PSScriptRoot 'mock-provider.cjs'
$fixture = Start-Process -FilePath (Get-Command node).Source -ArgumentList ('"' + $fixtureFile + '"') -PassThru -WindowStyle Hidden -RedirectStandardOutput (Join-Path $samplePath 'provider.log') -RedirectStandardError (Join-Path $samplePath 'provider-error.log')
Push-Location -LiteralPath $sourcePath
try {
    Write-Output "ZCode reference sample: $samplePath"
    & corepack pnpm --filter @zcode/desktop dev:runtime
} finally {
    Pop-Location
    if (!$fixture.HasExited) { Stop-Process -Id $fixture.Id }
}
