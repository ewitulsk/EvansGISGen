param(
    [switch]$SkipBuild,
    [string]$LevelType = 'geoworld:geoworld',
    [string]$Seed = '',
    [string]$Tag = ''
)
# Scripted terrain acceptance: launches a project-owned dedicated server with
# the geoworld:geoworld world preset, lets the terrain_survey scenario
# force-generate + inspect sample columns, and parses GEOWORLD-* markers.
# No desktop input; all evidence lands in artifacts/terrain-<timestamp>/.
# Note: not 'Stop' — java/gradle write benign output to stderr, which Windows
# PowerShell would surface as a terminating NativeCommandError. The script
# fails via explicit throws after checking GEOWORLD-RESULT.
$ErrorActionPreference = 'Continue'
$projectRoot = Split-Path -Parent $PSScriptRoot

# Gradle needs Java 21; a newer system JDK (e.g. Java 25) breaks Groovy.
$javaOk = $false
if ($env:JAVA_HOME -and (Test-Path (Join-Path $env:JAVA_HOME 'bin/java.exe'))) {
    $javaOk = (& (Join-Path $env:JAVA_HOME 'bin/java.exe') -version 2>&1 | Out-String) -match '"21\.'
}
if (-not $javaOk) {
    $jdk = Get-ChildItem 'C:\Program Files\Java\jdk-21*' -Directory -ErrorAction SilentlyContinue |
        Sort-Object Name -Descending | Select-Object -First 1
    if (-not $jdk) { throw 'Java 21 not found under C:\Program Files\Java\jdk-21*' }
    $env:JAVA_HOME = $jdk.FullName
}
$runId = 'terrain' + $(if ($Tag) { "-$Tag" }) + '-' + (Get-Date -Format 'yyyyMMdd-HHmmss-fff')
$artifactPath = Join-Path $projectRoot "artifacts/$runId"
$serverPath = Join-Path $artifactPath 'server'
$null = New-Item -ItemType Directory -Path $serverPath -Force
$null = New-Item -ItemType Directory -Path (Join-Path $serverPath 'config') -Force

# Dedicated server with the GeoWorld preset generating the overworld.
@('server-ip=127.0.0.1', 'server-port=0', 'enable-query=false', 'enable-rcon=false',
  'online-mode=false', 'view-distance=4', 'simulation-distance=4',
  "level-type=$LevelType", 'sync-chunk-writes=true', 'max-players=2',
  'spawn-protection=0', 'enforce-secure-profile=false') +
  $(if ($Seed) { "level-seed=$Seed" } else { @() }) |
    Set-Content (Join-Path $serverPath 'server.properties')
'eula=true' | Set-Content (Join-Path $serverPath 'eula.txt')

# GeoWorld runtime config: point at the beatrice dataset copied beside it.
@('{', '  "dataset_path": "geoworld/beatrice.geoworld"', '}') |
    Set-Content (Join-Path $serverPath 'config/geoworld.json')
Copy-Item -Recurse -LiteralPath (Join-Path $projectRoot 'datasets/beatrice.geoworld') `
    -Destination (Join-Path $serverPath 'geoworld/beatrice.geoworld')

$metadata = [ordered]@{
    runId = $runId
    startedUtc = [DateTime]::UtcNow.ToString('o')
    status = 'RUNNING'
    desktopInput = $false
    scenario = 'terrain_survey'
    assertions = @()
}
$metadata | ConvertTo-Json -Depth 5 | Set-Content (Join-Path $artifactPath 'result.json')

try {
    $logPath = Join-Path $artifactPath 'server.log'
    $tasks = @(':mod:runServer', "-PgeoworldServerRunDir=$serverPath",
               '-PgeoworldServerScenario=terrain_survey')
    if (-not $SkipBuild) { $tasks += ':mod:build' }
    & (Join-Path $projectRoot 'gradlew.bat') @tasks --console=plain 2>&1 |
        Tee-Object -FilePath $logPath
    $log = Get-Content -LiteralPath $logPath -Raw
    if ($log -notmatch 'GEOWORLD-RESULT (\w+)') { throw 'No GEOWORLD-RESULT marker in server log' }
    $result = $Matches[1]
    $asserts = [regex]::Matches($log, 'GEOWORLD-ASSERT (\S+) (PASS|FAIL)') |
        ForEach-Object { @{ name = $_.Groups[1].Value; status = $_.Groups[2].Value } }
    $metadata.assertions = $asserts
    $metadata.command = "gradlew :mod:runServer -PgeoworldServerRunDir=<artifact>/server -PgeoworldServerScenario=terrain_survey"
    $metadata.status = $result
    if ($result -ne 'PASS') {
        $failed = ($asserts | Where-Object { $_.status -ne 'PASS' } | ForEach-Object { $_.name }) -join ', '
        throw "Terrain survey result=$result failed=[$failed] (see $logPath)"
    }
} catch {
    $metadata.status = 'FAIL'
    $metadata.error = $_.Exception.Message
    throw
} finally {
    $metadata.finishedUtc = [DateTime]::UtcNow.ToString('o')
    $metadata | ConvertTo-Json -Depth 5 | Set-Content (Join-Path $artifactPath 'result.json')
    Write-Output "Scripted terrain artifacts: $artifactPath"
}
