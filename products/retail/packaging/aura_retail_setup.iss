; Aura Retail -- Windows installer (Wave 1B, Inno Setup 6).
;
; Build (after `pyinstaller products/retail/packaging/aura_retail.spec --noconfirm`,
; which must run first so dist/AuraRetail/ exists):
;   iscc products/retail/packaging/aura_retail_setup.iss
;
; AppId is a fixed, unique GUID -- this IS Inno Setup's upgrade key. Never
; change it for this product; a future release with a different AppId would
; install side-by-side instead of upgrading in place.
;
; Data safety: this installer only ever writes into {app} (by default
; %LOCALAPPDATA%\Programs\Action Aura\Aura Retail -- PrivilegesRequired=lowest
; makes {autopf} resolve per-user; an admin who picks "all users" at the dialog
; gets Program Files instead) and Start Menu/Desktop shortcuts + registry
; uninstall entries. (INSTALL-1: this header used to say Program Files only,
; which is not where a normal install goes.) It never
; touches %LOCALAPPDATA%\AuraRetail (the database/config/backups directory
; -- see products/retail/desktop/launcher_retail.py's AURA_APP_DATA
; resolution), so a default uninstall preserves all business data by
; construction, not by a special-cased exception. The one place this
; installer touches user data is the explicit, opt-in "delete my data"
; uninstall step below, which requires a typed confirmation.

#define AppVersion "1.0.0-rc.11"
#define AppId "{{A039EDA8-410E-4400-B3A5-A7CD4AF7F437}"
#define DistDir "..\..\..\dist\AuraRetail"

; Microsoft's WebView2 Evergreen bootstrapper (the small redistributable that
; downloads the runtime). NOT committed: scripts/release/build_desktop_installer.ps1
; fetches it from https://go.microsoft.com/fwlink/p/?LinkId=2124703, checks its
; Authenticode signature is a valid Microsoft one and prints its SHA-256. A
; manual `iscc` run needs that file first, so fail with a sentence, not a
; "source file not found" three screens down.
#define WebView2Setup "..\..\..\dist\redist\MicrosoftEdgeWebview2Setup.exe"
#if !FileExists(WebView2Setup)
  #error "dist\redist\MicrosoftEdgeWebview2Setup.exe is missing -- run scripts\release\build_desktop_installer.ps1 (it downloads and verifies it), or download it from https://go.microsoft.com/fwlink/p/?LinkId=2124703"
#endif

[Setup]
AppId={#AppId}
; Windows 10 1809 (build 17763) is the oldest build WebView2 and Python 3.12
; both support; an older Windows would install fine and then never open a
; window, which looks like a broken product rather than an unsupported PC.
MinVersion=10.0.17763
; The Aura mark (products/retail/frontend/brand/aura-app-icon.svg), rendered by
; scripts/release/make_app_icon.py. The exe carries the same icon (spec icon=),
; so the taskbar, Start menu, Explorer and this wizard all match.
SetupIconFile=aura_retail.ico
UninstallDisplayIcon={app}\AuraRetail.exe
AppName=Aura Retail
AppVersion={#AppVersion}
AppVerName=Aura Retail {#AppVersion}
AppPublisher=Action Aura
DefaultDirName={autopf}\Action Aura\Aura Retail
DefaultGroupName=Action Aura\Aura Retail
UninstallDisplayName=Aura Retail
OutputBaseFilename=AuraRetail-Setup-{#AppVersion}
OutputDir=..\..\..\dist\installers
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
DisableProgramGroupPage=yes
; No real Authenticode certificate available this wave (Wave 1B, Part H) --
; distributed as an unsigned release candidate, no SignTool directive
; configured. See docs/release/windows-code-signing-guide.md.
ArchitecturesInstallIn64BitMode=x64compatible
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog
; Update channel: the in-app updater runs this installer silently WHILE
; AuraRetail.exe is still open. CloseApplications=force (not the default "yes")
; closes it without a prompt a silent run could never answer, and the filter
; limits that to our own exe so nothing else on the machine is touched.
; RestartApplications=no: Inno's own restart only reopens apps it closed via
; the Restart Manager and is unreliable for a PyInstaller exe -- the [Run]
; entry below relaunches deterministically instead.
CloseApplications=force
CloseApplicationsFilter=AuraRetail.exe
RestartApplications=no

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "Create a &desktop shortcut"; GroupDescription: "Additional shortcuts:"; Flags: unchecked

[Files]
Source: "{#DistDir}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs
; A copy of THIS installer, kept beside the program. When the in-app updater
; later replaces this version with a newer one, it files this copy under
; updates\previous\ so "Go back to the previous version" has something to run
; (commercial_runtime/updates/rollback.py). {srcexe} + external = the running
; setup file itself, whatever it is called on the customer's disk. The running
; app prunes older copies on startup, so only the current version's stays.
Source: "{srcexe}"; DestDir: "{app}\installer-cache"; DestName: "AuraRetail-Setup-{#AppVersion}.exe"; Flags: external ignoreversion
; WebView2 bootstrapper: unpacked to {tmp} only when the runtime is missing,
; and deleted when setup ends.
Source: "{#WebView2Setup}"; DestDir: "{tmp}"; DestName: "MicrosoftEdgeWebview2Setup.exe"; Flags: deleteafterinstall; Check: WebView2Missing

[Icons]
Name: "{group}\Aura Retail"; Filename: "{app}\AuraRetail.exe"
Name: "{group}\Uninstall Aura Retail"; Filename: "{uninstallexe}"
Name: "{autodesktop}\Aura Retail"; Filename: "{app}\AuraRetail.exe"; Tasks: desktopicon

[Run]
; WebView2 runtime, only when absent (Windows 10 PCs that never got it). Listed
; FIRST so it finishes before any launch entry below. /silent /install runs
; unelevated here (PrivilegesRequired=lowest) and installs per-user; it needs
; the internet to fetch the runtime. If it cannot (offline shop), setup still
; succeeds and the launcher falls back to an Edge/Chrome app window
; (launcher_retail.py), so a failure here degrades the window, never the install.
Filename: "{tmp}\MicrosoftEdgeWebview2Setup.exe"; Parameters: "/silent /install"; StatusMsg: "Installing the Microsoft WebView2 runtime..."; Flags: waituntilterminated; Check: WebView2Missing
Filename: "{app}\AuraRetail.exe"; Description: "Launch Aura Retail now"; Flags: postinstall nowait skipifsilent
; Update channel: a silent install skips the postinstall entry above
; (skipifsilent), so the updater passes /RELAUNCH=1 and THIS entry starts the
; new build. Not "postinstall" on purpose -- postinstall entries are shown as a
; checkbox on the last page and are skipped when silent; a plain [Run] entry
; with a Check runs in both modes. A normal interactive install passes no
; /RELAUNCH, so it never double-launches alongside the checkbox.
; runasoriginaluser: a per-machine update (/ALLUSERS) runs this installer
; ELEVATED, and a non-postinstall [Run] entry inherits that by default. Aura
; would then start as the administrator -- and if the admin password typed
; at the UAC prompt belongs to a different Windows account, it would open that
; account's %LOCALAPPDATA%\AuraRetail: an empty shop. postinstall entries get
; this flag implicitly; this one must say it.
Filename: "{app}\AuraRetail.exe"; Flags: nowait runasoriginaluser; Check: ShouldRelaunch

[UninstallRun]
; Best-effort: make sure no lingering AuraRetail.exe (with its embedded
; server) survives uninstall. Ignore failure if it's not running.
Filename: "{cmd}"; Parameters: "/C taskkill /IM AuraRetail.exe /F"; Flags: runhidden skipifdoesntexist; RunOnceId: "KillAuraRetail"

[Code]
// WebView2 Evergreen runtime detection, per Microsoft's documented check: the
// client GUID's "pv" (product version) value exists and is not 0.0.0.0 under
// HKLM (64-bit view, 32-bit view = WOW6432Node) or HKCU (a per-user install).
const
  WebView2ClientKey = 'SOFTWARE\Microsoft\EdgeUpdate\Clients\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}';

function WebView2VersionOk(Root: Integer; const View32: Boolean): Boolean;
var
  Version: String;
  Found: Boolean;
begin
  if Root = HKEY_CURRENT_USER then
    Found := RegQueryStringValue(HKCU, WebView2ClientKey, 'pv', Version)
  else if View32 then
    Found := RegQueryStringValue(HKLM32, WebView2ClientKey, 'pv', Version)
  else
    Found := RegQueryStringValue(HKLM64, WebView2ClientKey, 'pv', Version);
  Result := Found and (Version <> '') and (Version <> '0.0.0.0');
end;

function WebView2Missing(): Boolean;
begin
  Result := not (WebView2VersionOk(HKEY_LOCAL_MACHINE, False)
              or WebView2VersionOk(HKEY_LOCAL_MACHINE, True)
              or WebView2VersionOk(HKEY_CURRENT_USER, False));
end;

// True only when the in-app updater started this installer (/RELAUNCH=1).
function ShouldRelaunch(): Boolean;
begin
  Result := ExpandConstant('{param:RELAUNCH|0}') = '1';
end;

// Custom "also delete my data" step -- unchecked/skipped by default, and
// even when the user opts in, requires an explicit second confirmation
// before anything under %LOCALAPPDATA%\AuraRetail is touched. This mirrors
// the mobile apps' restore-confirmation pattern (Wave 1A) rather than a
// single easy-to-miss checkbox.
function InitializeUninstall(): Boolean;
var
  DataDir: String;
begin
  Result := True;
  // Wave 1B fix: /SUPPRESSMSGBOXES (used by any scripted/managed uninstall,
  // not just this wave's own testing) auto-answers MsgBox with its default
  // button -- which is "Yes" for an unqualified MB_YESNO. Without this
  // guard, a silent uninstall would auto-confirm BOTH prompts below and
  // permanently delete business data with no human in the loop at all,
  // directly violating "never silently delete customer data." A silent
  // uninstall must never even ask; it must always default to preserving data.
  //
  // MUTATION-PROVED 2026-09-08, because a guard this consequential should not
  // be believed on the strength of its own comment. The three lines below were
  // deleted, the installer recompiled, and both versions were run through an
  // identical install + `unins000.exe /VERYSILENT /SUPPRESSMSGBOXES` cycle
  // against the real %LOCALAPPDATA%\AuraRetail (backed up first, restored
  // after -- testing this at any other path proves nothing, since that path is
  // the only one DelTree below ever names):
  //
  //     guard removed : uninstaller still running after 180s, killed
  //     guard present : uninstaller exit 0 after 4.6s, data 9 files -> 9 files
  //
  // So the guard is load-bearing and stays. One correction to the paragraph
  // above, which predicted the failure differently: with the guard removed the
  // uninstall did not silently auto-confirm and delete. It BLOCKED, on a
  // process named _unins.tmp holding a window titled "Uninstall" -- the first
  // MsgBox, waiting for a click that a scripted uninstall will never provide.
  // Data survived only because the uninstall never got past the question.
  // Either way a managed uninstall is broken without this line; the mechanism
  // is recorded because the next reader will otherwise trust a prediction that
  // this environment did not reproduce.
  if UninstallSilent() then
  begin
    Exit;
  end;
  DataDir := ExpandConstant('{localappdata}\AuraRetail');
  if DirExists(DataDir) then
  begin
    if MsgBox('Aura Retail is set up to remove. Your business data (products, sales, customers, backups) in:' + #13#10 + #13#10 +
      DataDir + #13#10 + #13#10 +
      'will be KEPT so you can reinstall later without losing anything.' + #13#10 + #13#10 +
      'Do you ALSO want to permanently delete that data now? This cannot be undone.',
      mbConfirmation, MB_YESNO) = IDYES then
    begin
      if MsgBox('This will permanently delete ALL Aura Retail business data on this computer. Type-equivalent final confirmation: are you absolutely sure?',
        mbError, MB_YESNO) = IDYES then
      begin
        DelTree(DataDir, True, True, True);
      end;
    end;
  end;
end;
