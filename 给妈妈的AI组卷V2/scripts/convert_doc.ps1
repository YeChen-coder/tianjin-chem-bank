param([Parameter(Mandatory=$true)][string]$InputPath,[Parameter(Mandatory=$true)][string]$OutputPath)
$ErrorActionPreference='Stop'
$outputAbsolute=[IO.Path]::GetFullPath($OutputPath)
$privateRoot=[IO.Path]::GetFullPath((Join-Path $PSScriptRoot '../.work/session-4'))
if (-not $outputAbsolute.StartsWith($privateRoot+[IO.Path]::DirectorySeparatorChar,[StringComparison]::OrdinalIgnoreCase)) { throw 'DOC conversion output must stay in session-4.' }
$existing=@(Get-Process WINWORD -ErrorAction SilentlyContinue | Select-Object -ExpandProperty Id)
$word=New-Object -ComObject Word.Application
$created=@(Get-Process WINWORD -ErrorAction SilentlyContinue | Where-Object {$_.Id -notin $existing})
if ($created.Count -ne 1) { [void][Runtime.InteropServices.Marshal]::ReleaseComObject($word); throw 'Separate Word instance could not be verified.' }
$word.Visible=$false;$word.DisplayAlerts=0;$word.AutomationSecurity=3;$doc=$null
try { $doc=$word.Documents.Open([IO.Path]::GetFullPath($InputPath),$false,$true,$false);$doc.SaveAs2($outputAbsolute,16) }
finally { if($null -ne $doc){$doc.Close(0);[void][Runtime.InteropServices.Marshal]::ReleaseComObject($doc)};$word.Quit();[void][Runtime.InteropServices.Marshal]::ReleaseComObject($word) }
