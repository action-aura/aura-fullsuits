# Best-effort desktop notification. A missing notifier must never crash the sync pass.

function Send-SyncNotification {
    param(
        [Parameter(Mandatory)][string]$Title,
        [Parameter(Mandatory)][string]$Message,
        [ValidateSet('Info', 'Warning', 'Error')][string]$Level = 'Info'
    )

    try {
        if (Get-Module -ListAvailable -Name BurntToast -ErrorAction SilentlyContinue) {
            Import-Module BurntToast -ErrorAction Stop
            New-BurntToastNotification -Text $Title, $Message
            return
        }
    } catch { }

    try {
        Add-Type -AssemblyName System.Windows.Forms -ErrorAction Stop
        $icon = New-Object System.Windows.Forms.NotifyIcon
        $icon.Icon = [System.Drawing.SystemIcons]::Information
        $icon.Visible = $true
        $winFormsLevel = switch ($Level) {
            'Error'   { [System.Windows.Forms.ToolTipIcon]::Error }
            'Warning' { [System.Windows.Forms.ToolTipIcon]::Warning }
            default   { [System.Windows.Forms.ToolTipIcon]::Info }
        }
        $icon.ShowBalloonTip(8000, $Title, $Message, $winFormsLevel)
        Start-Sleep -Milliseconds 300
        $icon.Dispose()
        return
    } catch { }

    # Last resort: console bell + title bar flag. Never throw.
    try {
        $Host.UI.RawUI.WindowTitle = "!! $Title !!"
        [Console]::Beep(880, 200)
    } catch { }
}
