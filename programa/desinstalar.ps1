# Desinstala Cementerio General. Los datos y respaldos en Documentos\Cementerio General NO se borran.
$ErrorActionPreference = 'SilentlyContinue'
Add-Type -AssemblyName PresentationFramework
$r = [System.Windows.MessageBox]::Show("¿Desinstalar el programa Cementerio General?`n`nSus datos y respaldos en Documentos\Cementerio General se conservan.", 'Cementerio General', 'YesNo', 'Question')
if ($r -ne 'Yes') { exit 0 }
if (Get-Process -Name 'Cementerio General') {
    [System.Windows.MessageBox]::Show('Cierre primero el programa Cementerio General.', 'Cementerio General', 'OK', 'Warning') | Out-Null
    exit 1
}
$destino = Join-Path $env:LOCALAPPDATA 'Programs\Cementerio General'
Remove-Item (Join-Path ([Environment]::GetFolderPath('Desktop')) 'Cementerio General.lnk') -Force
Remove-Item (Join-Path (Join-Path ([Environment]::GetFolderPath('StartMenu')) 'Programs') 'Cementerio General.lnk') -Force
Remove-Item 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Uninstall\CementerioGeneral' -Recurse -Force
Set-Location $env:TEMP
Remove-Item $destino -Recurse -Force
[System.Windows.MessageBox]::Show('Cementerio General fue desinstalado. Sus datos siguen en Documentos\Cementerio General.', 'Cementerio General', 'OK', 'Information') | Out-Null
