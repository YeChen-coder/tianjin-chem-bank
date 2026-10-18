param([Parameter(Mandatory=$true)][string]$InputPath,
      [Parameter(Mandatory=$true)][string]$OutputPath)
$ErrorActionPreference = 'Stop'
$inputAbsolute = [IO.Path]::GetFullPath($InputPath)
$outputAbsolute = [IO.Path]::GetFullPath($OutputPath)
$workspaceRoot = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '../.work/session-4'))
if (-not $outputAbsolute.StartsWith($workspaceRoot + [IO.Path]::DirectorySeparatorChar, [StringComparison]::OrdinalIgnoreCase)) {
    throw 'Word QA output must remain in V2 .work/session-4.'
}
[void](New-Item -ItemType Directory -Path (Split-Path $outputAbsolute -Parent) -Force)
$beforeHash = (Get-FileHash -LiteralPath $inputAbsolute -Algorithm SHA256).Hash
$existing = @(Get-Process WINWORD -ErrorAction SilentlyContinue | Select-Object -ExpandProperty Id)
$word = New-Object -ComObject Word.Application
$created = @(Get-Process WINWORD -ErrorAction SilentlyContinue | Where-Object { $_.Id -notin $existing })
if ($created.Count -ne 1) {
    [void][Runtime.InteropServices.Marshal]::ReleaseComObject($word)
    throw 'Could not verify a separate Word process; no existing instance was changed.'
}
$word.Visible = $false
$word.DisplayAlerts = 0
$word.AutomationSecurity = 3
$doc = $null
try {
    $doc = $word.Documents.Open($inputAbsolute, $false, $true, $false)
    $counts = @{ native_formulas=$doc.OMaths.Count; tables=$doc.Tables.Count; inline_shapes=$doc.InlineShapes.Count; pages=$doc.ComputeStatistics(2) }
    $doc.ExportAsFixedFormat($outputAbsolute, 17)
    $counts.input_sha256=$beforeHash.ToLower()
    $counts.output_pdf=$outputAbsolute
    $counts.word_version=$word.Version
    $counts | ConvertTo-Json | Set-Content -LiteralPath ([IO.Path]::ChangeExtension($outputAbsolute,'.word-qa.json')) -Encoding utf8
    $counts | ConvertTo-Json
} finally {
    if ($null -ne $doc) { $doc.Close(0); [void][Runtime.InteropServices.Marshal]::ReleaseComObject($doc) }
    $word.Quit()
    [void][Runtime.InteropServices.Marshal]::ReleaseComObject($word)
}
if ((Get-FileHash -LiteralPath $inputAbsolute -Algorithm SHA256).Hash -ne $beforeHash) { throw 'Source document changed unexpectedly.' }
