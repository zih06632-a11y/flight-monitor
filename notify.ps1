param(
  [string]$Title = "flight alert",
  [string]$Message = "",
  [string]$ReportPath = ""
)
$ErrorActionPreference = "SilentlyContinue"

# Channel 1 (guaranteed): durable log line.
$log = Join-Path $PSScriptRoot "data\alerts.log"
New-Item -ItemType Directory -Force -Path (Split-Path $log) | Out-Null
Add-Content -Path $log -Value ((Get-Date -Format "yyyy-MM-dd HH:mm:ss") + [char]9 + $Title + [char]9 + $Message) -Encoding UTF8

# Channel 2 (best effort): Windows toast. Silently no-ops when notifications are off,
# which is why nothing depends on it.
try {
  [Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, ContentType = WindowsRuntime] | Out-Null
  $tpl = [Windows.UI.Notifications.ToastNotificationManager]::GetTemplateContent([Windows.UI.Notifications.ToastTemplateType]::ToastText02)
  $nodes = $tpl.GetElementsByTagName("text")
  $nodes.Item(0).AppendChild($tpl.CreateTextNode($Title)) | Out-Null
  $nodes.Item(1).AppendChild($tpl.CreateTextNode($Message)) | Out-Null
  $toast = [Windows.UI.Notifications.ToastNotification]::new($tpl)
  $appId = "{1AC14E77-02E7-4E5D-B744-2EB1AE5198B7}\WindowsPowerShell\v1.0\powershell.exe"
  [Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier($appId).Show($toast)
} catch { }

# Channel 3 (guaranteed audible): system sound.
try { [System.Media.SystemSounds]::Exclamation.Play() } catch { }

# Channel 4 (opt-in): open the report so the alert is impossible to miss.
if ($ReportPath -and (Test-Path $ReportPath) -and $env:FLIGHT_MONITOR_OPEN_REPORT -eq "1") {
  Start-Process $ReportPath
}

exit 0
