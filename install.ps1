[CmdletBinding()]
param(
    [string]$PackagePath = '',
    [ValidateRange(0, 65535)]
    [int]$Port = 0
)

$ErrorActionPreference = 'Stop'

# Installs through N.E.K.O's package transaction API. Never writes runtime
# plugin directories directly, starts gameplay, or replaces an existing plugin.
if ([string]::IsNullOrWhiteSpace($PackagePath)) {
    $PackagePath = Join-Path $PSScriptRoot 'neko_buckshot.neko-plugin'
    if (-not (Test-Path -LiteralPath $PackagePath -PathType Leaf)) {
        $PackagePath = Join-Path (Split-Path -Parent $PSScriptRoot) 'neko_buckshot.neko-plugin'
    }
}

$httpClient = $null
$httpHandler = $null

try {
    $resolvedPackage = (Resolve-Path -LiteralPath $PackagePath).Path
    if (-not (Test-Path -LiteralPath $resolvedPackage -PathType Leaf)) {
        throw 'PackagePath must refer to a .neko-plugin file.'
    }
    if ([IO.Path]::GetExtension($resolvedPackage) -ne '.neko-plugin') {
        throw 'Only a .neko-plugin package is accepted.'
    }

    if ($Port -eq 0) {
        $Port = 48916
        if ($env:APPDATA) {
            $portConfigPath = Join-Path $env:APPDATA 'N.E.K.O\port_config.json'
            if (Test-Path -LiteralPath $portConfigPath -PathType Leaf) {
                try {
                    $portConfig = Get-Content -Raw -LiteralPath $portConfigPath | ConvertFrom-Json
                    $configuredPort = 0
                    if ([int]::TryParse([string]$portConfig.USER_PLUGIN_SERVER_PORT, [ref]$configuredPort) -and
                        $configuredPort -ge 1 -and $configuredPort -le 65535) {
                        $Port = $configuredPort
                    }
                } catch {
                    Write-Warning 'Could not read N.E.K.O port settings; using port 48916.'
                }
            }
        }
    }

    Add-Type -AssemblyName System.Net.Http
    $httpHandler = New-Object System.Net.Http.HttpClientHandler
    $httpHandler.UseProxy = $false
    $httpClient = New-Object System.Net.Http.HttpClient($httpHandler)
    $httpClient.Timeout = [TimeSpan]::FromSeconds(120)
    $baseUrl = 'http://127.0.0.1:' + $Port

    function Invoke-NekoJson {
        param([string]$Method, [string]$Path, $Body = $null)
        $request = New-Object System.Net.Http.HttpRequestMessage(
            (New-Object System.Net.Http.HttpMethod($Method)), ($baseUrl + $Path))
        $response = $null
        try {
            if ($null -ne $Body) {
                $json = ConvertTo-Json -InputObject $Body -Depth 20 -Compress
                $request.Content = New-Object System.Net.Http.StringContent(
                    $json, [Text.Encoding]::UTF8, 'application/json')
            }
            $response = $httpClient.SendAsync($request).GetAwaiter().GetResult()
            if (-not $response.IsSuccessStatusCode) {
                throw ('N.E.K.O API {0} {1} returned HTTP {2}. Open Plugin Center to inspect the error.' -f
                    $Method, $Path, [int]$response.StatusCode)
            }
            $bodyText = $response.Content.ReadAsStringAsync().GetAwaiter().GetResult()
            if ($bodyText) { return ($bodyText | ConvertFrom-Json) }
        } finally {
            if ($response) { $response.Dispose() }
            $request.Dispose()
        }
    }

    Write-Host ('Connecting to N.E.K.O on port {0}...' -f $Port)
    $null = Invoke-NekoJson -Method GET -Path '/plugin-cli/plugins'

    $multipart = New-Object System.Net.Http.MultipartFormDataContent
    $uploadResponse = $null
    try {
        $fileStream = [IO.File]::OpenRead($resolvedPackage)
        $fileContent = New-Object System.Net.Http.StreamContent($fileStream)
        $fileContent.Headers.ContentType = New-Object System.Net.Http.Headers.MediaTypeHeaderValue('application/octet-stream')
        $multipart.Add($fileContent, 'file', [IO.Path]::GetFileName($resolvedPackage))
        Write-Host 'Uploading package for validation...'
        $uploadResponse = $httpClient.PostAsync($baseUrl + '/plugin-cli/upload', $multipart).GetAwaiter().GetResult()
        if (-not $uploadResponse.IsSuccessStatusCode) {
            throw ('Package upload returned HTTP {0}. Open Plugin Center to inspect the error.' -f [int]$uploadResponse.StatusCode)
        }
        $uploaded = $uploadResponse.Content.ReadAsStringAsync().GetAwaiter().GetResult() | ConvertFrom-Json
    } finally {
        if ($uploadResponse) { $uploadResponse.Dispose() }
        # Disposes the file content and its underlying read-only stream too.
        $multipart.Dispose()
    }
    if (-not $uploaded.path) { throw 'N.E.K.O did not return an uploaded package path.' }

    $packageRef = @{ package = [string]$uploaded.path }
    $verified = Invoke-NekoJson -Method POST -Path '/plugin-cli/verify' -Body $packageRef
    if ($verified.ok -ne $true -or $verified.package_type -ne 'plugin' -or $verified.plugin_count -ne 1) {
        throw 'Package verification failed or the archive is not a single-plugin package.'
    }

    $plan = Invoke-NekoJson -Method POST -Path '/plugin-cli/install-plan' -Body $packageRef
    if ($plan.action -ne 'install') {
        throw ('Installation stopped: plan is "{0}". This helper only performs a first installation. To replace an existing version, explicitly import the package in N.E.K.O Plugin Center and review its upgrade confirmation.' -f $plan.action)
    }

    Write-Host ('Installing {0} {1}...' -f $plan.plugin_id, $plan.target_version)
    $installed = Invoke-NekoJson -Method POST -Path '/plugin-cli/install' -Body @{
        package = [string]$uploaded.path
        install_source = 'imported'
        on_conflict = 'fail'
        confirm_upgrade = $false
    }
    if ($installed.installed_plugin_count -ne 1) {
        throw 'N.E.K.O did not confirm installation of exactly one plugin.'
    }

    $pluginId = [string]$plan.plugin_id
    Write-Host ('Installed successfully: {0}' -f $pluginId) -ForegroundColor Green

    # Package installation and host discovery are separate steps. Refresh the
    # registry before checking it; a temporary discovery failure must never
    # misreport a committed installation as failed or prompt a reinstall.
    try {
        $null = Invoke-NekoJson -Method POST -Path '/plugins/refresh'
    } catch {
        Write-Warning 'The package is installed, but the host could not refresh its plugin list yet.'
    }
    $discovered = $false
    for ($attempt = 0; $attempt -lt 4; $attempt++) {
        try {
            $null = Invoke-NekoJson -Method GET -Path ('/plugins/' + [Uri]::EscapeDataString($pluginId))
            $discovered = $true
            break
        } catch {
            if ($attempt -lt 3) { Start-Sleep -Milliseconds 500 }
        }
    }
    if (-not $discovered) {
        Write-Warning 'Installation is complete; discovery is pending. Refresh Plugin Center or restart N.E.K.O. Do not reinstall the package.'
    }
    Write-Host 'Open N.E.K.O Plugin Center, refresh the plugin list, then start the plugin and open its panel.'
    Write-Host 'The installer does not start game observation or mouse control.'
    exit 0
} catch {
    Write-Host ('Installation failed: ' + $_.Exception.Message) -ForegroundColor Red
    Write-Host 'Keep N.E.K.O running, or import the .neko-plugin file manually in Plugin Center.'
    exit 1
} finally {
    if ($httpClient) { $httpClient.Dispose() }
    if ($httpHandler) { $httpHandler.Dispose() }
}
