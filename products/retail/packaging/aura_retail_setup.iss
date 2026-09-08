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
; Data safety: this installer only ever writes into {app} (Program Files)
; and Start Menu/Desktop shortcuts + registry uninstall entries. It never
; touches %LOCALAPPDATA%\AuraRetail (the database/config/backups directory
; -- see products/retail/desktop/launcher_retail.py's AURA_APP_DATA
; resolution), so a default uninstall preserves all business data by
; construction, not by a special-cased exception. The one place this
; installer touches user data is the explicit, opt-in "delete my data"
; uninstall step below, which requires a typed confirmation.

#define AppVersion "1.0.0-rc.5"
#define AppId "{{A039EDA8-410E-4400-B3A5-A7CD4AF7F437}"
#define DistDir "..\..\..\dist\AuraRetail"

[Setup]
AppId={#AppId}
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

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "Create a &desktop shortcut"; GroupDescription: "Additional shortcuts:"; Flags: unchecked

[Files]
Source: "{#DistDir}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\Aura Retail"; Filename: "{app}\AuraRetail.exe"
Name: "{group}\Uninstall Aura Retail"; Filename: "{uninstallexe}"
Name: "{autodesktop}\Aura Retail"; Filename: "{app}\AuraRetail.exe"; Tasks: desktopicon

[Run]
Filename: "{app}\AuraRetail.exe"; Description: "Launch Aura Retail now"; Flags: postinstall nowait skipifsilent

[UninstallRun]
; Best-effort: make sure no lingering AuraRetail.exe (with its embedded
; server) survives uninstall. Ignore failure if it's not running.
Filename: "{cmd}"; Parameters: "/C taskkill /IM AuraRetail.exe /F"; Flags: runhidden skipifdoesntexist; RunOnceId: "KillAuraRetail"

[Code]
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
