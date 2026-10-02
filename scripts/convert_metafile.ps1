param([Parameter(Mandatory=$true)][string]$InputPath,
      [Parameter(Mandatory=$true)][string]$OutputPath)
$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName System.Drawing
$Metafile = [System.Drawing.Image]::FromFile($InputPath)
try {
    $Scale = [Math]::Min(2.0, 1400.0 / [Math]::Max($Metafile.Width, $Metafile.Height))
    $Width = [Math]::Max(1, [int]($Metafile.Width * $Scale))
    $Height = [Math]::Max(1, [int]($Metafile.Height * $Scale))
    $Bitmap = New-Object System.Drawing.Bitmap($Width, $Height)
    try {
        $Graphics = [System.Drawing.Graphics]::FromImage($Bitmap)
        try {
            $Graphics.Clear([System.Drawing.Color]::White)
            $Graphics.DrawImage($Metafile, 0, 0, $Width, $Height)
        } finally { $Graphics.Dispose() }
        $Bitmap.Save($OutputPath, [System.Drawing.Imaging.ImageFormat]::Png)
    } finally { $Bitmap.Dispose() }
} finally { $Metafile.Dispose() }
