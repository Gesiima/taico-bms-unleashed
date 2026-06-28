# ================================
#  Taico BMS unleashed Release Builder
# ================================

Write-Host "Enter release version (e.g. 0.8.0):"
$version = Read-Host

if (-not $version) {
    Write-Host "No version entered. Aborting."
    exit 1
}

# Validate version format: must be X.Y.Z
if ($version -notmatch '^\d+\.\d+\.\d+$') {
    Write-Host "Invalid version format. Use X.Y.Z (e.g. 0.8.0)."
    exit 1
}

Write-Host "Building release v$version ..."
$manifest = "release.manifest"

if (-not (Test-Path $manifest)) {
    Write-Host "ERROR: release.manifest not found!"
    exit 1
}

$files = Get-Content $manifest

# Check if all files exist
foreach ($item in $files) {
    if (-not (Test-Path $item)) {
        Write-Host "ERROR: File or folder missing: $item"
        exit 1
    }
}

$zipName = "taico-bms-unleashed-$version.zip"

Compress-Archive -Path $files -DestinationPath $zipName -Force

Write-Host "Release created: $zipName"
