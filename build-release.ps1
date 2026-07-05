# ================================
#  Taico BMS unleashed Release Builder
# ================================
# Version wird automatisch aus vkbms/__init__.py gelesen.
# Optional ueberschreiben:  .\build-release.ps1 -Version 0.11.1

param([string]$Version)

$initFile = "vkbms/__init__.py"

if (-not $Version) {
    if (-not (Test-Path $initFile)) {
        Write-Host "ERROR: $initFile nicht gefunden - Version kann nicht ermittelt werden."
        exit 1
    }
    $content = Get-Content $initFile -Raw
    if ($content -match '__version__\s*=\s*["'']([0-9]+\.[0-9]+\.[0-9]+)["'']') {
        $Version = $Matches[1]
        Write-Host "Version aus $initFile erkannt: v$Version"
    } else {
        Write-Host "ERROR: __version__ in $initFile nicht gefunden."
        exit 1
    }
}

# Format pruefen: X.Y.Z
if ($Version -notmatch '^\d+\.\d+\.\d+$') {
    Write-Host "Ungueltiges Versionsformat. Erwartet X.Y.Z (z. B. 0.11.1)."
    exit 1
}

Write-Host "Building release v$Version ..."
$manifest = "release.manifest"

if (-not (Test-Path $manifest)) {
    Write-Host "ERROR: release.manifest not found!"
    exit 1
}

$files = Get-Content $manifest | Where-Object { $_.Trim() -ne "" }

# Pruefen, ob alle Eintraege existieren
foreach ($item in $files) {
    if (-not (Test-Path $item)) {
        Write-Host "ERROR: File or folder missing: $item"
        exit 1
    }
}

$zipName = "taico-bms-unleashed-$Version.zip"

Compress-Archive -Path $files -DestinationPath $zipName -Force

Write-Host "Release created: $zipName"
