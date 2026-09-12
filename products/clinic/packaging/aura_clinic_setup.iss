; Aura Clinic -- Windows installer (Wave 1B, Inno Setup 6).
; Mirrors products/retail/packaging/aura_retail_setup.iss -- see that file's
; comments for the full rationale (fixed AppId as the upgrade key, why data
; safety holds by construction, the opt-in delete-data confirmation flow).
;
; Build (after `pyinstaller products/clinic/packaging/aura_clinic.spec --noconfirm`):
;   iscc products/clinic/packaging/aura_clinic_setup.iss

#define AppVersion "1.0.0-rc.7"
#define AppId "{{159905F6-CEB8-4A5F-B5C3-D0190159F679}"
#define DistDir "..\..\..\dist\AuraClinic"

[Setup]
AppId={#AppId}
AppName=Aura Clinic
AppVersion={#AppVersion}
AppVerName=Aura Clinic {#AppVersion}
AppPublisher=Action Aura
DefaultDirName={autopf}\Action Aura\Aura Clinic
DefaultGroupName=Action Aura\Aura Clinic
UninstallDisplayName=Aura Clinic
OutputBaseFilename=AuraClinic-Setup-{#AppVersion}
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
Name: "{group}\Aura Clinic"; Filename: "{app}\AuraClinic.exe"
Name: "{group}\Uninstall Aura Clinic"; Filename: "{uninstallexe}"
Name: "{autodesktop}\Aura Clinic"; Filename: "{app}\AuraClinic.exe"; Tasks: desktopicon

[Run]
Filename: "{app}\AuraClinic.exe"; Description: "Launch Aura Clinic now"; Flags: postinstall nowait skipifsilent

[UninstallRun]
Filename: "{cmd}"; Parameters: "/C taskkill /IM AuraClinic.exe /F"; Flags: runhidden skipifdoesntexist; RunOnceId: "KillAuraClinic"

[Code]
// Patient data is materially more sensitive than Retail's business data --
// same opt-in-with-double-confirmation pattern, worded for clinical records.
function InitializeUninstall(): Boolean;
var
  DataDir: String;
begin
  Result := True;
  // Wave 1B fix: see aura_retail_setup.iss's identical guard for the full
  // rationale -- /SUPPRESSMSGBOXES auto-answers MsgBox with its default
  // button ("Yes"), so a silent uninstall must never even show the
  // delete-data prompt, or it would auto-delete patient data unattended.
  if UninstallSilent() then
  begin
    Exit;
  end;
  DataDir := ExpandConstant('{localappdata}\AuraClinic');
  if DirExists(DataDir) then
  begin
    if MsgBox('Aura Clinic is set up to remove. Your clinic data (patients, appointments, invoices, backups) in:' + #13#10 + #13#10 +
      DataDir + #13#10 + #13#10 +
      'will be KEPT so you can reinstall later without losing anything.' + #13#10 + #13#10 +
      'Do you ALSO want to permanently delete that data now? This cannot be undone.',
      mbConfirmation, MB_YESNO) = IDYES then
    begin
      if MsgBox('This will permanently delete ALL Aura Clinic patient and business data on this computer. Final confirmation: are you absolutely sure?',
        mbError, MB_YESNO) = IDYES then
      begin
        DelTree(DataDir, True, True, True);
      end;
    end;
  end;
end;
