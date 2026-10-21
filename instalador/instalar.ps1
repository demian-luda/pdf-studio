# Instala PDF Studio para el usuario actual, sin permisos de administrador:
#   - copia la aplicación a %LOCALAPPDATA%\Programs\PDF Studio
#   - crea los accesos directos en el menú Inicio y (si no se pide lo contrario) en el escritorio
#   - la agrega a «Aplicaciones instaladas» y a «Abrir con» para los PDF
# Uso: doble clic en Instalar.cmd, o desde PowerShell:
#   powershell -NoProfile -ExecutionPolicy Bypass -File .\instalar.ps1 [-SinEscritorio]
# -Raiz: instalación de prueba en esa carpeta (accesos directos ahí y registro en
#        HKCU:\Software\PDFStudioPrueba), sin tocar el menú Inicio ni el escritorio reales.
param([switch]$SinEscritorio, [string]$Raiz = "")

$ErrorActionPreference = "Stop"
$origen = Join-Path $PSScriptRoot "PDFStudio"
$exeOrigen = Join-Path $origen "PDFStudio.exe"
if (-not (Test-Path $exeOrigen)) {
    Write-Host "No se encontró PDFStudio\PDFStudio.exe al lado de este script. Descomprimí el ZIP completo y volvé a probar." -ForegroundColor Red
    exit 1
}
$version = "?"
$archivoVersion = Join-Path $PSScriptRoot "VERSION.txt"
if (Test-Path $archivoVersion) { $version = (Get-Content $archivoVersion -TotalCount 1).Trim() }

if ($Raiz) {
    $destino = Join-Path $Raiz "Programs\PDF Studio"
    $carpetaMenu = Join-Path $Raiz "Menu"
    $carpetaEscritorio = Join-Path $Raiz "Escritorio"
    $registro = "HKCU:\Software\PDFStudioPrueba"
    New-Item -ItemType Directory -Force $carpetaMenu, $carpetaEscritorio | Out-Null
} else {
    $destino = Join-Path $env:LOCALAPPDATA "Programs\PDF Studio"
    $carpetaMenu = [Environment]::GetFolderPath("Programs")
    $carpetaEscritorio = [Environment]::GetFolderPath("Desktop")
    $registro = "HKCU:\Software"
}
$exe = Join-Path $destino "PDFStudio.exe"

$abiertos = Get-Process -Name PDFStudio -ErrorAction SilentlyContinue | Where-Object { $_.Path -and $_.Path -like "$destino*" }
if ($abiertos) {
    Write-Host "PDF Studio está abierto. Cerralo y volvé a ejecutar el instalador." -ForegroundColor Yellow
    exit 1
}

Write-Host "Instalando PDF Studio $version en $destino ..."
if (Test-Path $destino) { Remove-Item -Recurse -Force $destino }
New-Item -ItemType Directory -Force $destino | Out-Null
Copy-Item -Recurse -Force (Join-Path $origen "*") $destino
foreach ($extra in "desinstalar.ps1", "LICENSE.txt", "LICENCIAS-TERCEROS.txt", "LEEME.md", "VERSION.txt") {
    $ruta = Join-Path $PSScriptRoot $extra
    if (Test-Path $ruta) { Copy-Item -Force $ruta $destino }
}

# Accesos directos
$shell = New-Object -ComObject WScript.Shell
function Crear-Acceso([string]$ruta) {
    $acceso = $shell.CreateShortcut($ruta)
    $acceso.TargetPath = $exe
    $acceso.WorkingDirectory = $destino
    $acceso.IconLocation = "$exe,0"
    $acceso.Description = "PDF Studio: editor de PDF libre y gratuito"
    $acceso.Save()
}
Crear-Acceso (Join-Path $carpetaMenu "PDF Studio.lnk")
if (-not $SinEscritorio) {
    Crear-Acceso (Join-Path $carpetaEscritorio "PDF Studio.lnk")
}

# «Aplicaciones instaladas» (solo para este usuario)
$clave = "$registro\Microsoft\Windows\CurrentVersion\Uninstall\PDFStudio"
New-Item -Force $clave | Out-Null
$desinstalar = "powershell.exe -NoProfile -ExecutionPolicy Bypass -File `"$destino\desinstalar.ps1`""
if ($Raiz) { $desinstalar += " -Raiz `"$Raiz`"" }
$valores = @{
    DisplayName = "PDF Studio"; DisplayVersion = $version; Publisher = "PDF Studio (software libre, AGPL-3.0)";
    DisplayIcon = "$exe,0"; InstallLocation = $destino; UninstallString = $desinstalar
}
foreach ($nombre in $valores.Keys) { Set-ItemProperty -Path $clave -Name $nombre -Value $valores[$nombre] }
Set-ItemProperty -Path $clave -Name NoModify -Value 1 -Type DWord
Set-ItemProperty -Path $clave -Name NoRepair -Value 1 -Type DWord

# «Abrir con» para los PDF (no cambia el programa predeterminado)
$app = "$registro\Classes\Applications\PDFStudio.exe"
New-Item -Force "$app\shell\open\command" | Out-Null
Set-ItemProperty -Path "$app\shell\open\command" -Name "(default)" -Value "`"$exe`" `"%1`""
Set-ItemProperty -Path $app -Name FriendlyAppName -Value "PDF Studio"
New-Item -Force "$app\SupportedTypes" | Out-Null
Set-ItemProperty -Path "$app\SupportedTypes" -Name ".pdf" -Value ""
New-Item -Force "$registro\Classes\.pdf\OpenWithList\PDFStudio.exe" | Out-Null

Write-Host ""
Write-Host "Listo: PDF Studio quedó instalado." -ForegroundColor Green
Write-Host "Abrilo desde el menú Inicio o el escritorio. Para desinstalarlo: Configuración > Aplicaciones > PDF Studio."
Write-Host "Para el OCR hace falta Tesseract: la app lo detecta y ofrece instalarlo (panel Faltantes)."
