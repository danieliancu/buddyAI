<#
.SYNOPSIS
    Regenerates the BuddyAI LVGL fonts built from Noto Sans / FontAwesome.

.DESCRIPTION
    Uses lv_font_conv 1.5.2 (via npx, needs Node.js) and patches the include
    guard so the files compile as part of the ESP-IDF "ui" component.

    buddy_font_20 / _28  Noto Sans Medium + FontAwesome 5 symbols (ICON_*).
                         ASCII, Latin-1, Latin Extended-A/B, Latin Extended
                         Additional (Vietnamese), Greek, Cyrillic, general
                         punctuation and the euro sign. CJK / Arabic / Hebrew /
                         Indic scripts are NOT included - the watch hides
                         captions it cannot render (ui.c).
    buddy_font_big       Noto Sans Medium 56 px, ASCII + £ ° µ € (short answer
                         values shown large in the chat).
    buddy_math_20/28/56  Maths and science symbols, used as the fallback font of
                         the three fonts above (a separate font so tall glyphs such
                         as the integral sign do not change their line height):
                         superscripts / subscripts, letterlike symbols (℃ Ω ℏ),
                         number forms (⅓) from Noto Sans Medium; arrows, mathematical
                         operators and a few technical symbols from Noto Sans Math.
    buddy_font_shortcut  FontAwesome 5, 34 px: pen, calendar, gear (watchface icons).

    The other fonts (buddy_font_clock / _code / _icon) are unchanged; their
    exact commands are kept in the header comment of each .c file.

    Required files in -FontDir:
      NotoSans-Medium.ttf       https://github.com/notofonts/notofonts.github.io/raw/main/fonts/NotoSans/hinted/ttf/NotoSans-Medium.ttf
      NotoSansMath-Regular.ttf  https://github.com/notofonts/notofonts.github.io/raw/main/fonts/NotoSansMath/hinted/ttf/NotoSansMath-Regular.ttf
      FontAwesome5.woff         copy of managed_components/lvgl__lvgl/scripts/built_in_font/FontAwesome5-Solid+Brands+Regular.woff

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
$math  = Join-Path $FontDir "NotoSansMath-Regular.ttf"
$icons = Join-Path $FontDir "FontAwesome5.woff"
foreach ($f in @($text, $math, $icons)) {
    if (-not (Test-Path $f)) { throw "Missing font file: $f" }
}

# Text ranges (must stay in sync with the caption glyph check in ui.c - it
# checks the font at runtime, so extending these ranges needs no code change).
$textRanges = "0x20-0x7E,0xA0-0x24F,0x1E00-0x1EFF,0x370-0x3FF,0x400-0x4FF,0x2010-0x2027,0x20AC"
# FontAwesome 5 symbols used by ui_priv.h (ICON_*) and the LVGL keyboard (LV_SYMBOL_BACKSPACE /
# NEW_LINE / KEYBOARD, plus OK / CLOSE / LEFT / RIGHT above). Keep identical for all sizes.
$iconRanges = "0xF1EB,0xF240-0xF244,0xF0E7,0xF013,0xF028,0xF027,0xF185,0xF1FC,0xF0AC,0xF071,0xF021,0xF00C,0xF00D,0xF127,0xF023,0xF130,0xF053,0xF054,0xF304,0xF073,0xF55A,0xF8A2,0xF11C"
# Short answer values: ASCII + £ ° µ €.
$bigRanges = "0x20-0x7E,0xA3,0xB0,0xB5,0x20AC"
# Maths / science fallback: superscripts & subscripts, letterlike, number forms (Noto Sans) ...
$mathTextRanges = "0x2070-0x209F,0x2100-0x214F,0x2150-0x218F"
# ... arrows, mathematical operators, ceiling / floor / diameter, angle brackets (Noto Sans Math).
$mathRanges = "0x2190-0x21FF,0x2200-0x22FF,0x2300-0x230B,0x27E8-0x27E9"

New-Item -ItemType Directory -Force $OutDir | Out-Null

# Run lv_font_conv, then make the file compile in the IDF component and keep
# local paths out of its header. $fallback: name of the font to fall back to.
function New-Font([string]$name, [int]$size, [string[]]$sources, [string]$fallback = "") {
    $out = Join-Path $OutDir "$name.c"
    Write-Host "Generating $name ($size px) -> $out"
    & npx --yes lv_font_conv@1.5.2 --bpp 4 --no-compress --format lvgl --size $size @sources -o $out
    if ($LASTEXITCODE -ne 0) { throw "lv_font_conv failed for $name" }

    $src = [System.IO.File]::ReadAllText($out)
    # lvgl is an IDF component: always include "lvgl.h".
    $src = $src.Replace("#ifdef LV_LVGL_H_INCLUDE_SIMPLE", "#if 1 /* BuddyAI: lvgl is an IDF component */")
    $src = $src.Replace($text, "NotoSans-Medium.ttf").Replace($math, "NotoSansMath-Regular.ttf")
    $src = $src.Replace($icons, "FontAwesome5.woff").Replace($out, "$name.c")
    if ($fallback) {
        $src = $src.Replace("/*Initialize a public general font descriptor*/",
            "extern const lv_font_t $fallback;   /* BuddyAI: maths / science symbols */`n`n/*Initialize a public general font descriptor*/")
        $src = $src.Replace("    .dsc = &font_dsc ", "    .fallback = &$fallback,`n    .dsc = &font_dsc ")
        if ($src -notmatch "\.fallback = &$fallback") { throw "could not add the fallback to $name" }
    }
    [System.IO.File]::WriteAllText($out, $src, (New-Object System.Text.UTF8Encoding($false)))
}

foreach ($size in @(20, 28, 56)) {
    New-Font "buddy_math_$size" $size @("--font", $text, "-r", $mathTextRanges, "--font", $math, "-r", $mathRanges)
}
foreach ($size in @(20, 28)) {
    New-Font "buddy_font_$size" $size @("--font", $text, "-r", $textRanges, "--font", $icons, "-r", $iconRanges) "buddy_math_$size"
}
New-Font "buddy_font_big" 56 @("--font", $text, "-r", $bigRanges) "buddy_math_56"
New-Font "buddy_font_shortcut" 34 @("--font", $icons, "-r", "0xF304,0xF073,0xF013")

Write-Host "Done."
