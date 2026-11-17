; StemForge per-user installer (ticket 21). Inno Setup 6.x.
;
; package.ps1 compiles this with:
;   ISCC.exe /DVersion=<semver> /DWebView2Installer=<verified bootstrapper> stemforge.iss
;
; Design rules from ticket 10 §6:
;   * per-user install under %LOCALAPPDATA%\Programs\StemForge, no UAC;
;   * Windows 10 22H2 (build 19045) or newer, x64 only;
;   * WebView2 Evergreen Bootstrapper embedded as dontcopy and run only when
;     the runtime is absent;
;   * the Edge Chromium backend is the only supported engine;
;   * uninstall removes program files but never %LOCALAPPDATA%\StemForge.

#ifndef Version
  #error /DVersion=<semver> is required; package.ps1 supplies stemforge.__version__
#endif
#ifndef WebView2Installer
  #error /DWebView2Installer=<path to MicrosoftEdgeWebview2Setup.exe> is required
#endif

#define AppName "StemForge"
#define AppPublisher "StemForge contributors"
#define AppURL "https://github.com/alexmarincu/stemforge"
#define AppExeName "StemForge.exe"
#define BundleDir "..\..\dist\StemForge"
#define DataDir "{localappdata}\StemForge"

[Setup]
AppId={{7E2F6C2A-6D1B-4A57-9B6D-2B0E4A2F6C21}
AppName={#AppName}
AppVersion={#Version}
AppVerName={#AppName} {#Version}
AppPublisher={#AppPublisher}
AppPublisherURL={#AppURL}
AppSupportURL={#AppURL}/issues
AppUpdatesURL={#AppURL}/releases
DefaultDirName={localappdata}\Programs\StemForge
DefaultGroupName={#AppName}
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=10.0.19045
OutputDir=..\..\dist
OutputBaseFilename=StemForge-{#Version}-win64-setup
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
SetupIconFile=..\..\src\stemforge\assets\stemforge.ico
UninstallDisplayIcon={app}\StemForge.exe
UninstallDisplayName={#AppName} {#Version}
CloseApplications=yes
RestartApplications=no
AllowNoIcons=yes

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"; Flags: unchecked

[Files]
Source: "{#BundleDir}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs
; Embedded, not installed. InstallWebView2() extracts and runs it only when the
; Evergreen Runtime is absent.
Source: "{#WebView2Installer}"; DestDir: "{tmp}"; Flags: dontcopy

[Icons]
Name: "{group}\{#AppName}"; Filename: "{app}\{#AppExeName}"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\{#AppExeName}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#AppExeName}"; Description: "{cm:LaunchProgram,{#StringChange(AppName, '&', '&&')}}"; Flags: nowait postinstall skipifsilent

; Deliberately no [UninstallDelete] entry for {#DataDir}: models, cache, logs,
; and settings must survive uninstall. Inno removes the [Files] entries and the
; [Icons] entries automatically; the uninstaller then tells the user where the
; kept data lives.

[Code]
const
  WebView2ClientId = '{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}';
  WebView2HelpUrl = 'https://developer.microsoft.com/microsoft-edge/webview2/';
  StemForgeBackend = 'edgechromium';

function WebView2Version(): String;
var
  Value: String;
begin
  Result := '';
  if RegQueryStringValue(HKCU, 'Software\Microsoft\EdgeUpdate\Clients\' + WebView2ClientId, 'pv', Value) then
    Result := Value
  else if RegQueryStringValue(HKLM, 'Software\Microsoft\EdgeUpdate\Clients\' + WebView2ClientId, 'pv', Value) then
    Result := Value
  else if RegQueryStringValue(HKLM, 'Software\WOW6432Node\Microsoft\EdgeUpdate\Clients\' + WebView2ClientId, 'pv', Value) then
    Result := Value;
end;

function DotNet48Installed(): Boolean;
var
  Release: Cardinal;
begin
  Result :=
    RegQueryDWordValue(HKLM, 'Software\Microsoft\NET Framework Setup\NDP\v4\Full', 'Release', Release)
    and (Release >= 394802);
end;

procedure InstallWebView2();
var
  Installer: String;
  ResultCode: Integer;
begin
  if WebView2Version() <> '' then
    Exit;

  Installer := ExpandConstant('{tmp}\MicrosoftEdgeWebview2Setup.exe');
  ExtractTemporaryFile('MicrosoftEdgeWebview2Setup.exe');
  if (not Exec(Installer, '/silent /install', '', SW_SHOWNORMAL, ewWaitUntilTerminated, ResultCode))
     or (ResultCode <> 0) or (WebView2Version() = '') then
  begin
    MsgBox(
      'StemForge could not install the Microsoft Edge WebView2 Runtime automatically.' + #13#10 +
      'StemForge needs it to show its window; the ' + StemForgeBackend + ' backend is required.' + #13#10#13#10 +
      'Install it from ' + WebView2HelpUrl + ', then start StemForge again.',
      mbError, MB_OK);
  end;
end;

procedure CurStepChanged(CurStep: TSetupStep);
begin
  if CurStep = ssPostInstall then
  begin
    InstallWebView2();
    if not DotNet48Installed() then
      MsgBox(
        'StemForge needs .NET Framework 4.8 or newer.' + #13#10 +
        'Enable it in Windows Features, then start StemForge again.',
        mbError, MB_OK);
  end;
end;

procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
begin
  if CurUninstallStep = usPostUninstall then
    MsgBox(
      'Your StemForge data was kept at:' + #13#10 + ExpandConstant('{#DataDir}') + #13#10#13#10 +
      'Delete that folder manually if you also want to remove the model, cache, logs, and settings.',
      mbInformation, MB_OK);
end;
