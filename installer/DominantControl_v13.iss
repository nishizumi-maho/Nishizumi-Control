#define AppName "Dominant Control"
#define AppVersion "13"
#define AppExeName "DominantControl.exe"
#define AppSourceDir "..\dist\DominantControl_Portable"
#define AppOutputDir "..\dist\installer"

[Setup]
; No AppId is declared on purpose: Inno then uses AppName as the identity, so
; this installer keeps updating the same "Dominant Control" installation
; instead of creating a second product beside it.
AppName={#AppName}
AppVersion={#AppVersion}
AppVerName={#AppName} v{#AppVersion}
AppPublisher=Nishizumi
AppSupportURL=https://github.com/nishizumi-maho/Nishizumi-Control
AppUpdatesURL=https://github.com/nishizumi-maho/Nishizumi-Control/releases
DefaultDirName={localappdata}\Programs\{#AppName}
DefaultGroupName={#AppName}
DisableDirPage=no
DisableProgramGroupPage=yes
OutputDir={#AppOutputDir}
OutputBaseFilename=DominantControl_v13_Setup
UninstallDisplayName={#AppName} v{#AppVersion}
UninstallDisplayIcon={app}\{#AppExeName}
Compression=lzma2/ultra64
SolidCompression=yes
WizardStyle=modern
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
MinVersion=10.0
CloseApplications=yes
RestartApplications=no
SetupLogging=yes
VersionInfoVersion=13.0.0.0
VersionInfoCompany=Nishizumi
VersionInfoDescription=Dominant Control installer
VersionInfoProductName=Dominant Control
VersionInfoProductVersion=13

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "Create a desktop shortcut"; GroupDescription: "Additional options:"; Flags: unchecked
Name: "startup"; Description: "Start Dominant Control with Windows"; GroupDescription: "Additional options:"

[Files]
Source: "{#AppSourceDir}\*"; DestDir: "{app}"; Excludes: "\portable.mode,\data\*"; Flags: ignoreversion recursesubdirs createallsubdirs

[InstallDelete]
; Replace only the application's own files and leave the user's data alone.
Type: files; Name: "{app}\{#AppExeName}"
Type: files; Name: "{app}\portable.mode"
Type: filesandordirs; Name: "{app}\_internal"
Type: filesandordirs; Name: "{app}\external_overlays"

[Icons]
Name: "{autoprograms}\{#AppName}"; Filename: "{app}\{#AppExeName}"; WorkingDir: "{app}"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\{#AppExeName}"; WorkingDir: "{app}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#AppExeName}"; Description: "Open {#AppName} now"; Flags: nowait postinstall skipifsilent

[UninstallDelete]
Type: files; Name: "{userstartup}\DominantControl.bat"

[Code]
function IsProcessRunning(FileName: string): Boolean;
var
  Locator: Variant;
  WMIService: Variant;
  Processes: Variant;
begin
  Result := False;
  try
    Locator := CreateOleObject('WbemScripting.SWbemLocator');
    WMIService := Locator.ConnectServer('.', 'root\CIMV2');
    Processes := WMIService.ExecQuery(
      'SELECT * FROM Win32_Process WHERE Name="' + FileName + '"'
    );
    Result := Processes.Count > 0;
  except
    Result := False;
  end;
end;

function IsApplicationRunning(): Boolean;
begin
  Result :=
    IsProcessRunning('DominantControl.exe') or
    IsProcessRunning('NishizumiTireOriginal.exe');
end;

function PrepareToInstall(var NeedsRestart: Boolean): String;
begin
  Result := '';
  while IsApplicationRunning() do
  begin
    if MsgBox(
      'Dominant Control or one of its overlays is still open. ' +
      'Close it and click Retry.',
      mbConfirmation,
      MB_RETRYCANCEL
    ) = IDCANCEL then
    begin
      Result := 'Setup was cancelled because Dominant Control is still open.';
      Exit;
    end;
  end;
end;

function JsonBool(Enabled: Boolean): string;
begin
  if Enabled then
    Result := 'true'
  else
    Result := 'false';
end;

procedure SaveStartupPreference(Enabled: Boolean);
var
  ConfigDir: string;
  ConfigFile: string;
  Text: string;
  TextFile: AnsiString;
  Changed: Integer;
  InsertPos: Integer;
begin
  ConfigDir := ExpandConstant('{userappdata}\DominantControl\configs');
  ConfigFile := ConfigDir + '\config_v3.json';
  ForceDirectories(ConfigDir);

  if FileExists(ConfigFile) then
  begin
    if LoadStringFromFile(ConfigFile, TextFile) then
      Text := TextFile
    else
      Text := '{}';
  end
  else
    Text := '{}';

  if Trim(Text) = '' then
    Text := '{}';

  Changed := StringChangeEx(Text, '"start_with_windows": true', '"start_with_windows": ' + JsonBool(Enabled), True);
  Changed := Changed + StringChangeEx(Text, '"start_with_windows": false', '"start_with_windows": ' + JsonBool(Enabled), True);
  Changed := Changed + StringChangeEx(Text, '"start_with_windows":true', '"start_with_windows": ' + JsonBool(Enabled), True);
  Changed := Changed + StringChangeEx(Text, '"start_with_windows":false', '"start_with_windows": ' + JsonBool(Enabled), True);

  if Changed = 0 then
  begin
    if Trim(Text) = '{}' then
      Text := '{' + #13#10 + '    "start_with_windows": ' + JsonBool(Enabled) + #13#10 + '}'
    else if Pos('"start_with_windows"', Text) = 0 then
    begin
      InsertPos := Length(Text);
      while (InsertPos > 0) and (Copy(Text, InsertPos, 1) <> '}') do
        InsertPos := InsertPos - 1;
      if InsertPos > 0 then
        Insert(',' + #13#10 + '    "start_with_windows": ' + JsonBool(Enabled) + #13#10, Text, InsertPos);
    end;
  end;

  TextFile := Text;
  SaveStringToFile(ConfigFile, TextFile, False);
end;

procedure SaveStartupBatch(Enabled: Boolean);
var
  StartupBat: string;
  Content: string;
begin
  StartupBat := ExpandConstant('{userstartup}\DominantControl.bat');
  if Enabled then
  begin
    ForceDirectories(ExtractFileDir(StartupBat));
    Content := '@echo off' + #13#10 +
      'start "" "' + ExpandConstant('{app}\{#AppExeName}') + '"' + #13#10;
    SaveStringToFile(StartupBat, Content, False);
  end
  else if FileExists(StartupBat) then
    DeleteFile(StartupBat);
end;

procedure CurStepChanged(CurStep: TSetupStep);
var
  StartupEnabled: Boolean;
begin
  if CurStep = ssPostInstall then
  begin
    StartupEnabled := WizardIsTaskSelected('startup');
    SaveStartupPreference(StartupEnabled);
    SaveStartupBatch(StartupEnabled);
  end;
end;

procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
begin
  if CurUninstallStep = usUninstall then
  begin
    SaveStartupPreference(False);
    SaveStartupBatch(False);
  end;
end;
