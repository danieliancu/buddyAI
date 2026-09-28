<#
.SYNOPSIS
    Regenerates the BuddyAI LVGL text fonts (buddy_font_20 / buddy_font_28).

.DESCRIPTION
    Uses lv_font_conv 1.5.2 (via npx, needs Node.js) to convert Noto Sans Medium
    plus the FontAwesome 5 symbol subset into LVGL C fonts, then patches the
    include guard so the files compile as part of the ESP-IDF "ui" component.

    Coverage: ASCII, Latin-1, Latin Extended-A/B, Latin Extended Additional
    (Vietnamese), Greek, Cyrillic, general punctuation (dashes, quotes,
    ellipsis) and the euro sign. CJK / Arabic / Hebrew / Indic scripts are NOT
    included - the watch hides captions it cannot render (ui.c).

    The other fonts (buddy_font_clock / _code / _icon) are unchanged; their
    exact commands are kept in the header comment of each .c file.

    Required files in -FontDir:
      NotoSans-Medium.ttf   https://github.com/notofonts/notofonts.github.io/raw/main/fonts/NotoSans/hinted/ttf/NotoSans-Medium.ttf
      FontAwesome5.woff     copy of managed_components/lvgl__lvgl/scripts/built_in_font/FontAwesome5-Solid+Brands+Regular.woff

.PARAMETER FontDir
    Folder containing the TTF/WOFF source files.

.PARAMETER OutDir
    Destination for the generated .c files (default: components/ui/fonts).

.EXAMPLE
    .\tools\gen_fonts.ps1 -FontDir C:\temp\fonts
#>
param(
    [Parameter(Mandatory = $true)][string]$FontDir,
    [string]$OutDir = (Join-Path $PSScriptRoot "..\components\ui\fonts")
)

$ErrorActionPreference = "Stop"

$text  = Join-Path $FontDir "NotoSans-Medium.ttf"
$icons = Join-Path $FontDir "FontAwesome5.woff"
foreach ($f in @($text, $icons)) {
    if (-not (Test-Path $f)) { throw "Missing font file: $f" }
}

# Text ranges (must stay in sync with the caption glyph check in ui.c - it
# checks the font at runtime, so extending these ranges needs no code change).
$textRanges = "0x20-0x7E,0xA0-0x24F,0x1E00-0x1EFF,0x370-0x3FF,0x400-0x4FF,0x2010-0x2027,0x20AC"
# FontAwesome 5 symbols used by ui_priv.h (ICON_*). Keep identical for all sizes.
$iconRanges = "0xF1EB,0xF240-0xF244,0xF0E7,0xF013,0xF028,0xF027,0xF185,0xF1FC,0xF0AC,0xF071,0xF021,0xF00C,0xF00D,0xF127,0xF023,0xF130,0xF053,0xF054"

New-Item -ItemType Directory -Force $OutDir | Out-Null

foreach ($size in @(20, 28)) {
    $name = "buddy_font_$size"
    $out  = Join-Path $OutDir "$name.c"
    Write-Host "Generating $name ($size px) -> $out"
    & npx --yes lv_font_conv@1.5.2 `
        --bpp 4 --no-compress --format lvgl --size $size `
        --font $text  -r $textRanges `
        --font $icons -r $iconRanges `
        -o $out
    if ($LASTEXITCODE -ne 0) { throw "lv_font_conv failed for $name" }

    # lvgl is an IDF component: always include "lvgl.h".
    $src = [System.IO.File]::ReadAllText($out)
    $src = $src.Replace("#ifdef LV_LVGL_H_INCLUDE_SIMPLE", "#if 1 /* BuddyAI: lvgl is an IDF component */")
    # Keep the header comment free of local absolute paths.
    $src = $src.Replace($text, "NotoSans-Medium.ttf").Replace($icons, "FontAwesome5.woff").Replace($out, "$name.c")
    [System.IO.File]::WriteAllText($out, $src, (New-Object System.Text.UTF8Encoding($false)))
}

Write-Host "Done."
