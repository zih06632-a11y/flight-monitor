param([string]$Title = "flight alert", [string]$Message = "")
$ErrorActionPreference = 'SilentlyContinue'
$log = Join-Path $PSScriptRoot "data\alerts.log"
New-Item -ItemType Directory -Force -Path (Split-Path $log) | Out-Null
$line = (Get-Date -Format "yyyy-MM-dd HH:mm:ss") + [char]9 + $Title + [char]9 + $Message
Add-Content -Path $log -Value $line
try {
  [Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, ContentType = WindowsRuntime] | Out-Null
  $tpl = [Windows.UI.Notifications.ToastNotificationManager]::GetTemplateContent([Windows.UI.Notifications.ToastTemplateType]::ToastText02)
  $nodes = $tpl.GetElementsByTagName("text")
  $nodes.Item(0).AppendChild($tpl.CreateTextNode($Title)) | Out-Null
  $nodes.Item(1).AppendChild($tpl.CreateTextNode($Message)) | Out-Null
  $toast = [Windows.UI.Notifications.ToastNotification]::new($tpl)
  $appId = '{1AC14E77-02E7-4E5D-B744-2EB1AE5198B7}\WindowsPowerShell\v1.0\powershell.exe'
  [Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier($appId).Show($toast)
  exit 0
} catch {
  try {
    Add-Type -AssemblyName System.Windows.Forms
    $ni = New-Object System.Windows.Forms.NotifyIcon
    $ni.Icon = [System.Drawing.SystemIcons]::Information
    $ni.Visible = $true
    $ni.ShowBalloonTip(15000, $Title, $Message, [System.Windows.Forms.ToolTipIcon]::Info)
    Start-Sleep -Seconds 6
    $ni.Dispose()
  } catch { }
}
