# Overlaycraft media helper (Windows 10/11, PowerShell 5.1).
# Reads the system media sessions (what the Windows volume flyout shows: browsers,
# Telegram Desktop, Spotify...) and prints one JSON line whenever they change.
# Commands come on stdin, one per line: "toggle|play|pause|next|prev|stop <session id>", "quit".
$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
[Console]::InputEncoding = [System.Text.Encoding]::UTF8

Add-Type -AssemblyName System.Runtime.WindowsRuntime
$asTask = ([System.WindowsRuntimeSystemExtensions].GetMethods() | Where-Object {
    $_.Name -eq 'AsTask' -and $_.GetParameters().Count -eq 1 -and
    $_.GetParameters()[0].ParameterType.Name -eq 'IAsyncOperation`1' })[0]
function Await($op, [Type]$type) {
    $t = $asTask.MakeGenericMethod($type).Invoke($null, @($op))
    if (-not $t.Wait(3000)) { return $null }
    $t.Result
}

$null = [Windows.Media.Control.GlobalSystemMediaTransportControlsSessionManager, Windows.Media.Control, ContentType = WindowsRuntime]
$Manager = [Windows.Media.Control.GlobalSystemMediaTransportControlsSessionManager]
$Props = [Windows.Media.Control.GlobalSystemMediaTransportControlsSessionMediaProperties]
$mgr = Await ($Manager::RequestAsync()) $Manager
if ($null -eq $mgr) { '{"error":"no media manager"}'; exit 1 }

function Sessions {
    $current = $mgr.GetCurrentSession()
    $list = @()
    foreach ($s in $mgr.GetSessions()) {
        $p = $null
        try { $p = Await ($s.TryGetMediaPropertiesAsync()) $Props } catch { }
        $info = $s.GetPlaybackInfo()
        $tl = $s.GetTimelineProperties()
        $pos = $tl.Position.TotalSeconds
        # Position is a snapshot: move it on by the time since it was taken while playing
        if ($info.PlaybackStatus -eq 'Playing' -and $tl.LastUpdatedTime.Year -gt 2000) {
            $pos += ([DateTimeOffset]::Now - $tl.LastUpdatedTime).TotalSeconds
        }
        $c = $info.Controls
        $list += [ordered]@{
            id      = $s.SourceAppUserModelId
            title   = if ($p) { $p.Title } else { '' }
            artist  = if ($p) { $p.Artist } else { '' }
            album   = if ($p) { $p.AlbumTitle } else { '' }
            status  = [string]$info.PlaybackStatus
            pos     = [math]::Round([math]::Max(0, $pos), 1)
            dur     = [math]::Round(($tl.EndTime - $tl.StartTime).TotalSeconds, 1)
            current = ($null -ne $current -and $current.SourceAppUserModelId -eq $s.SourceAppUserModelId)
            canNext = $c.IsNextEnabled
            canPrev = $c.IsPreviousEnabled
        }
    }
    , $list
}

function Run([string]$line) {
    $parts = $line.Trim().Split(' ', 2)
    $cmd = $parts[0]
    $id = if ($parts.Count -gt 1) { $parts[1] } else { '' }
    $target = $null
    foreach ($s in $mgr.GetSessions()) { if ($s.SourceAppUserModelId -eq $id) { $target = $s } }
    if ($null -eq $target) { $target = $mgr.GetCurrentSession() }
    if ($null -eq $target) { return }
    $op = switch ($cmd) {
        'toggle' { $target.TryTogglePlayPauseAsync() }
        'play'   { $target.TryPlayAsync() }
        'pause'  { $target.TryPauseAsync() }
        'next'   { $target.TrySkipNextAsync() }
        'prev'   { $target.TrySkipPreviousAsync() }
        'stop'   { $target.TryStopAsync() }
        default  { $null }
    }
    if ($op) { $null = Await $op ([bool]) }
}

# Console.In.ReadLineAsync blocks in .NET Framework; a StreamReader over the raw stream does not
$reader = New-Object System.IO.StreamReader([Console]::OpenStandardInput(), [System.Text.Encoding]::UTF8)
$pending = $reader.ReadLineAsync()
$last = ''
$lastAt = [DateTime]::MinValue
while ($true) {
    while ($pending.IsCompleted) {
        $line = $pending.Result
        if ($null -eq $line -or $line -eq 'quit') { exit 0 }
        try { Run $line } catch { }
        $pending = $reader.ReadLineAsync()
        $lastAt = [DateTime]::MinValue
    }
    try {
        $list = Sessions
        $json = ConvertTo-Json -Compress -Depth 4 @{ sessions = $list }
        # The mod moves the position on by itself: print when anything else changes,
        # and every 5 s anyway so the position stays in sync
        foreach ($s in $list) { $s.pos = 0 }
        $key = ConvertTo-Json -Compress -Depth 4 @{ sessions = $list }
        if ($key -ne $last -or ([DateTime]::Now - $lastAt).TotalSeconds -ge 5) {
            [Console]::Out.WriteLine($json)
            [Console]::Out.Flush()
            $last = $key
            $lastAt = [DateTime]::Now
        }
    } catch {
        [Console]::Out.WriteLine((ConvertTo-Json -Compress @{ error = $_.Exception.Message }))
    }
    $null = $pending.Wait(700)
}
