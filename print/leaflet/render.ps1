# Renders leaflet.html to leaflet.pdf (print) and preview-1.png / preview-2.png (screen, for review).
$here = Split-Path -Parent $MyInvocation.MyCommand.Path
$edge = "C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"
$url = "file:///" + ((Join-Path $here "leaflet.html") -replace '\\', '/')
$common = @("--headless=new", "--disable-gpu", "--no-first-run", "--hide-scrollbars", "--user-data-dir=$env:TEMP\leaflet-edge")

function Run-Edge([string[]]$extra) {
    $p = Start-Process $edge -ArgumentList ($common + $extra) -PassThru -WindowStyle Hidden
    if (-not $p.WaitForExit(60000)) { $p.Kill() }
}

Run-Edge @("--no-pdf-header-footer", "--print-to-pdf=$here\leaflet.pdf", $url)
foreach ($n in 1, 2) {
    Run-Edge @("--force-device-scale-factor=2", "--window-size=794,1123", "--screenshot=$here\preview-$n.png", "$($url)?page=$n")
}
Get-ChildItem $here -File | Where-Object { $_.Extension -in ".pdf", ".png" } | Select-Object Name, Length, LastWriteTime
