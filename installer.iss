; CutLab — installer Windows (Inno Setup 6)
; Compila con:  iscc installer.iss     (oppure build_installer.ps1)

#define AppName "CutLab"
#define AppVersion "1.0.0"
#define AppPublisher "CutLab"
#define AppId "{{7C1B4E22-9A55-4F3E-8B62-3E4A1D9C5F21}"

[Setup]
AppId={#AppId}
AppName={#AppName}
AppVersion={#AppVersion}
AppVerName={#AppName} {#AppVersion}
AppPublisher={#AppPublisher}
DefaultDirName={localappdata}\Programs\CutLab
DefaultGroupName=CutLab
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
OutputDir=dist
OutputBaseFilename=CutLabSetup
SetupIconFile=assets\cutlab.ico
UninstallDisplayIcon={app}\assets\cutlab.ico
UninstallDisplayName={#AppName}
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=10.0
DisableDirPage=auto
DisableWelcomePage=no
AppMutex=CutLabAppMutex

[Languages]
Name: "italian"; MessagesFile: "compiler:Languages\Italian.isl"
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"

[Files]
Source: "app.pyw";          DestDir: "{app}"; Flags: ignoreversion
Source: "server.py";        DestDir: "{app}"; Flags: ignoreversion
Source: "resolve_api.py";   DestDir: "{app}"; Flags: ignoreversion
Source: "analyze.py";       DestDir: "{app}"; Flags: ignoreversion
Source: "cutplan.py";       DestDir: "{app}"; Flags: ignoreversion
Source: "build.py";         DestDir: "{app}"; Flags: ignoreversion
Source: "README.md";        DestDir: "{app}"; Flags: ignoreversion skipifsourcedoesntexist
Source: "requirements.txt"; DestDir: "{app}"; Flags: ignoreversion skipifsourcedoesntexist
Source: "web\*";            DestDir: "{app}\web"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "assets\cutlab.ico"; DestDir: "{app}\assets"; Flags: ignoreversion

[Icons]
Name: "{group}\CutLab"; Filename: "{code:GetPythonW}"; Parameters: """{app}\app.pyw"""; WorkingDir: "{app}"; IconFilename: "{app}\assets\cutlab.ico"
Name: "{group}\Disinstalla CutLab"; Filename: "{uninstallexe}"
Name: "{autodesktop}\CutLab"; Filename: "{code:GetPythonW}"; Parameters: """{app}\app.pyw"""; WorkingDir: "{app}"; IconFilename: "{app}\assets\cutlab.ico"; Tasks: desktopicon

[Run]
Filename: "{code:GetPythonW}"; Parameters: """{app}\app.pyw"""; WorkingDir: "{app}"; Description: "Avvia CutLab"; Flags: nowait postinstall skipifsilent

[UninstallDelete]
Type: filesandordirs; Name: "{app}\out"
Type: filesandordirs; Name: "{app}\__pycache__"

[Code]
var
  PythonW: String;
  DepsOK: Boolean;
  DepsMsg: String;

function CandidatePythonExe(Version: String; Root: Integer; Silent: Boolean): String;
var
  P: String;
begin
  Result := '';
  if RegQueryStringValue(Root, 'SOFTWARE\Python\PythonCore\' + Version + '\InstallPath',
                         'ExecutablePath', P) and FileExists(P) then
    Result := P;
end;

function TestDeps(PyExe: String): Boolean;
var
  Code: Integer;
begin
  Exec(ExpandConstant('{cmd}'),
       '/c ""' + PyExe + '" -c "import fastapi,uvicorn,webview,numpy,faster_whisper" > nul 2>&1"',
       '', SW_HIDE, ewWaitUntilTerminated, Code);
  Result := (Code = 0);
end;

function WindowedOf(PyExe: String): String;
begin
  Result := ExtractFilePath(PyExe) + 'pythonw.exe';
  if not FileExists(Result) then
    Result := PyExe;
end;

procedure ResolvePython();
var
  Versions: array[0..5] of String;
  Roots: array[0..1] of Integer;
  i, j, k: Integer;
  Py, FirstFound: String;
begin
  Versions[0] := '3.13'; Versions[1] := '3.12'; Versions[2] := '3.11';
  Versions[3] := '3.10'; Versions[4] := '3.9';  Versions[5] := '3.8';
  Roots[0] := HKCU; Roots[1] := HKLM;
  FirstFound := '';
  DepsOK := False;
  DepsMsg := '';

  for k := 0 to 5 do
    for j := 0 to 1 do
    begin
      Py := CandidatePythonExe(Versions[k], Roots[j], False);
      if Py = '' then
        Continue;
      if FirstFound = '' then
        FirstFound := Py;
      if TestDeps(Py) then
      begin
        PythonW := WindowedOf(Py);
        DepsOK := True;
        Exit;
      end;
    end;

  if FirstFound <> '' then
  begin
    PythonW := WindowedOf(FirstFound);
    DepsMsg := 'Interprete trovato: ' + FirstFound;
    Exit;
  end;

  PythonW := 'pythonw.exe';
  DepsMsg := 'Nessuna installazione Python trovata nel registro.';
end;

function GetPythonW(Param: String): String;
begin
  if PythonW = '' then
    ResolvePython();
  Result := PythonW;
end;

function InitializeSetup(): Boolean;
begin
  ResolvePython();
  Result := True;
  if not DepsOK and (Pos('pythonw.exe', PythonW) = 0) then
  begin
    MsgBox('CutLab richiede Python 3.10 o superiore.' + #13#10 +
           'Installalo da https://www.python.org/downloads/ e rilancia questo installer.',
           mbError, MB_OK);
    Result := False;
    Exit;
  end;
  if not DepsOK then
    if MsgBox('Alcune dipendenze Python non risultano installate.' + #13#10 + #13#10 +
              DepsMsg + #13#10 + #13#10 +
              'Puoi installare CutLab ugualmente e sistemarle dopo con:' + #13#10 +
              '    pip install -r requirements.txt' + #13#10 + #13#10 + 'Continuare?',
              mbConfirmation, MB_YESNO) = IDNO then
      Result := False;
end;
