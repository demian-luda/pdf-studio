# Desinstala PDF Studio del usuario actual: la carpeta de la aplicación, los accesos directos y
# las entradas del registro. NO borra tus datos (firmas guardadas, preferencias) ni tus PDFs:
# las firmas quedan en %APPDATA%\PDFStudio por si volvés a instalar.
# Uso: Configuración > Aplicaciones > PDF Studio > Desinstalar, o desde PowerShell:
#   powershell -NoProfile -ExecutionPolicy Bypass -File "$env:LOCALAPPDATA\Programs\PDF Studio\desinstalar.ps1"
# -Raiz: desinstala una instalación de prueba (ver instalar.ps1).
param([string]$Raiz = "")

$ErrorActionPreference = "Stop"
if ($Raiz) {
    $destino = Join-Path $Raiz "Programs\PDF Studio"
    $carpetas = (Join-Path $Raiz "Menu"), (Join-Path $Raiz "Escritorio")
    $registro = "HKCU:\Software\PDFStudioPrueba"
} else {
    $destino = Join-Path $env:LOCALAPPDATA "Programs\PDF Studio"
    $carpetas = [Environment]::GetFolderPath("Programs"), [Environment]::GetFolderPath("Desktop")
    $registro = "HKCU:\Software"
}

$abiertos = Get-Process -Name PDFStudio -ErrorAction SilentlyContinue | Where-Object { $_.Path -and $_.Path -like "$destino*" }
if ($abiertos) {
    Write-Host "PDF Studio está abierto. Cerralo y volvé a ejecutar el desinstalador." -ForegroundColor Yellow
    exit 1
}

foreach ($carpeta in $carpetas) {
    $acceso = Join-Path $carpeta "PDF Studio.lnk"
    if (Test-Path $acceso) { Remove-Item -Force $acceso }
}
foreach ($clave in "$registro\Microsoft\Windows\CurrentVersion\Uninstall\PDFStudio",
                   "$registro\Classes\Applications\PDFStudio.exe",
                   "$registro\Classes\.pdf\OpenWithList\PDFStudio.exe") {
    if (Test-Path $clave) { Remove-Item -Recurse -Force $clave }
}
if ($Raiz -and (Test-Path "HKCU:\Software\PDFStudioPrueba")) { Remove-Item -Recurse -Force "HKCU:\Software\PDFStudioPrueba" }
# Este script está dentro de la carpeta: se sale de ella antes de borrarla
Set-Location $env:TEMP
if (Test-Path $destino) { Remove-Item -Recurse -Force $destino }

Write-Host "PDF Studio se desinstaló. Tus firmas guardadas siguen en $env:APPDATA\PDFStudio." -ForegroundColor Green
