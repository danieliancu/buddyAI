# Generates speech WAV samples (16 kHz mono) with Windows SAPI voices for fake_watch.py.
# Romanian samples use an English voice (only to exercise VAD/latency); record real Romanian
# speech (e.g. with Windows Voice Recorder -> ro_*.wav) to evaluate Romanian STT quality.
Add-Type -AssemblyName System.Speech
$out = Join-Path $PSScriptRoot "..\tests\samples"
New-Item -ItemType Directory -Force $out | Out-Null
$fmt = New-Object System.Speech.AudioFormat.SpeechAudioFormatInfo(16000, [System.Speech.AudioFormat.AudioBitsPerSample]::Sixteen, [System.Speech.AudioFormat.AudioChannel]::Mono)
$samples = @{
  "en_1" = "What will the weather be like tomorrow?";
  "en_2" = "Remind me what time my first meeting is.";
  "en_3" = "Tell me a short fun fact about the ocean.";
  "ro_1" = "Ce vreme va fi mâine în București?";
  "ro_2" = "Spune-mi o glumă scurtă.";
}
foreach ($k in $samples.Keys) {
  $s = New-Object System.Speech.Synthesis.SpeechSynthesizer
  $s.SelectVoice("Microsoft Zira Desktop")
  $s.SetOutputToWaveFile((Join-Path $out "$k.wav"), $fmt)
  $s.Speak($samples[$k])
  $s.Dispose()
}
Get-ChildItem $out
