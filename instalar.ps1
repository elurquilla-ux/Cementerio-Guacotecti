# Instalador de Cementerio General (no requiere permisos de administrador)
# Descarga el motor del programa (Electron) desde su sitio oficial, verifica su huella SHA-256
# y coloca el programa en %LOCALAPPDATA%\Programs\Cementerio General.
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
Add-Type -AssemblyName PresentationFramework
Add-Type -AssemblyName System.IO.Compression.FileSystem

$Version = '38.2.0'
$Sha256  = '4382B317DBBBC0BBF8A301304749324B88207218AAC240B670F1C1247C2A02B0'
$Url     = "https://github.com/electron/electron/releases/download/v$Version/electron-v$Version-win32-x64.zip"

function Aviso($texto, $icono = 'Information') { [System.Windows.MessageBox]::Show($texto, 'Cementerio General', 'OK', $icono) | Out-Null }
function Paso($texto) { Write-Host ''; Write-Host "  $texto" -ForegroundColor Cyan }

Write-Host ''
Write-Host '  ==========================================' -ForegroundColor DarkGreen
Write-Host '   Instalando Cementerio General' -ForegroundColor White
Write-Host '  ==========================================' -ForegroundColor DarkGreen

try {
    $programa = Join-Path $PSScriptRoot 'programa'
    $destino  = Join-Path $env:LOCALAPPDATA 'Programs\Cementerio General'
    $exe      = Join-Path $destino 'Cementerio General.exe'

    if (-not (Test-Path (Join-Path $programa 'main.js'))) {
        Aviso "No se encontró la carpeta 'programa' junto a este instalador.`n`nDescomprima todo el archivo .zip (clic derecho > Extraer todo) y vuelva a ejecutar Instalar." 'Error'
        exit 1
    }
    if (Get-Process -Name 'Cementerio General' -ErrorAction SilentlyContinue) {
        Aviso "El programa Cementerio General está abierto.`n`nCiérrelo (así se guarda el respaldo) y vuelva a ejecutar Instalar." 'Warning'
        exit 1
    }

    # ¿Ya está instalado el motor de esta versión? Entonces solo se actualiza el programa.
    $archivoVersion = Join-Path $destino 'version'
    $tieneMotor = (Test-Path $exe) -and (Test-Path $archivoVersion) -and ((Get-Content $archivoVersion -Raw).Trim() -eq $Version)

    if (-not $tieneMotor) {
        $zip = Join-Path $env:TEMP "cementerio_motor_$Version.zip"
        $listo = (Test-Path $zip) -and ((Get-FileHash $zip -Algorithm SHA256).Hash -eq $Sha256)
        if (-not $listo) {
            Paso 'Descargando los componentes del programa (unos 120 MB).'
            Write-Host '  Esto puede tardar varios minutos según su conexión a internet...'
            try {
                Start-BitsTransfer -Source $Url -Destination $zip -DisplayName 'Cementerio General' -Description 'Descargando componentes'
            } catch {
                Write-Host '  Probando otro método de descarga...'
                (New-Object System.Net.WebClient).DownloadFile($Url, $zip)
            }
            Paso 'Verificando la descarga...'
            if ((Get-FileHash $zip -Algorithm SHA256).Hash -ne $Sha256) {
                Remove-Item $zip -Force -ErrorAction SilentlyContinue
                throw 'La descarga llegó incompleta o dañada. Revise la conexión a internet y vuelva a ejecutar Instalar.'
            }
        }
        Paso 'Instalando...'
        if (Test-Path $destino) { Remove-Item $destino -Recurse -Force }
        New-Item -ItemType Directory -Force -Path $destino | Out-Null
        [System.IO.Compression.ZipFile]::ExtractToDirectory($zip, $destino)
        Rename-Item -Path (Join-Path $destino 'electron.exe') -NewName 'Cementerio General.exe'
        Remove-Item (Join-Path $destino 'resources\default_app.asar') -Force -ErrorAction SilentlyContinue
        Get-ChildItem (Join-Path $destino 'locales') -File |
            Where-Object { @('es.pak', 'es-419.pak', 'en-US.pak') -notcontains $_.Name } |
            Remove-Item -Force
        Remove-Item $zip -Force -ErrorAction SilentlyContinue
    } else {
        Paso 'Actualizando el programa...'
    }

    # Archivos del programa (los datos están en Documentos y no se tocan)
    $app = Join-Path $destino 'resources\app'
    if (Test-Path $app) { Remove-Item $app -Recurse -Force }
    New-Item -ItemType Directory -Force -Path $app | Out-Null
    Get-ChildItem $programa -File | Where-Object { $_.Name -ne 'desinstalar.ps1' } | Copy-Item -Destination $app -Force
    Copy-Item (Join-Path $programa 'icono.ico') -Destination $destino -Force
    Copy-Item (Join-Path $programa 'desinstalar.ps1') -Destination $destino -Force
    Get-ChildItem -Path $destino -Recurse -File | Unblock-File

    # Accesos directos en el escritorio y en el menú Inicio
    Paso 'Creando accesos directos...'
    $shell = New-Object -ComObject WScript.Shell
    $carpetas = @([Environment]::GetFolderPath('Desktop'), (Join-Path ([Environment]::GetFolderPath('StartMenu')) 'Programs'))
    foreach ($c in $carpetas) {
        $lnk = $shell.CreateShortcut((Join-Path $c 'Cementerio General.lnk'))
        $lnk.TargetPath = $exe
        $lnk.WorkingDirectory = $destino
        $lnk.IconLocation = (Join-Path $destino 'icono.ico') + ',0'
        $lnk.Description = 'Control de títulos y espacios del cementerio'
        $lnk.Save()
    }

    # Registro en "Aplicaciones instaladas" para poder desinstalar
    $clave = 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Uninstall\CementerioGeneral'
    New-Item -Path $clave -Force | Out-Null
    $desinstalar = 'powershell.exe -NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File "' + (Join-Path $destino 'desinstalar.ps1') + '"'
    Set-ItemProperty -Path $clave -Name DisplayName -Value 'Cementerio General'
    Set-ItemProperty -Path $clave -Name DisplayVersion -Value '1.2.0'
    Set-ItemProperty -Path $clave -Name Publisher -Value 'Unidad de Cementerios'
    Set-ItemProperty -Path $clave -Name DisplayIcon -Value (Join-Path $destino 'icono.ico')
    Set-ItemProperty -Path $clave -Name InstallLocation -Value $destino
    Set-ItemProperty -Path $clave -Name UninstallString -Value $desinstalar
    Set-ItemProperty -Path $clave -Name NoModify -Value 1 -Type DWord
    Set-ItemProperty -Path $clave -Name NoRepair -Value 1 -Type DWord

    Paso 'Listo.'
    Aviso "Cementerio General quedó instalado.`n`nEncontrará el acceso directo en el escritorio y en el menú Inicio.`nSus datos se guardan en Documentos\Cementerio General."
    Start-Process -FilePath $exe -WorkingDirectory $destino
}
catch {
    Write-Host ''
    Write-Host ('  Error: ' + $_.Exception.Message) -ForegroundColor Red
    Aviso ("No se pudo completar la instalación:`n`n" + $_.Exception.Message) 'Error'
    exit 1
}
