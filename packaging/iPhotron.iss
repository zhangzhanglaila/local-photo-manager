; iPhotron installer (Inno Setup 6).
;
; Installs the Nuitka standalone build into the user's local programs folder so no
; administrator rights are required, and creates desktop + Start-menu shortcuts.
; User data (the workspace: index, thumbnails, embeddings) is deliberately NOT part
; of the install directory — it lives wherever the user points the first-run wizard.

#define AppName "iPhotron"
#define AppExeName "iPhoto.exe"
#define AppPublisher "iPhotron"
#define AppVersion "1.0.0"

[Setup]
AppId={{8F3C1B24-9A64-4C8E-9E51-2B7D3A5C1F10}
AppName={#AppName}
AppVersion={#AppVersion}
AppPublisher={#AppPublisher}
DefaultDirName={localappdata}\Programs\iPhotron
DefaultGroupName={#AppName}
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
OutputDir=..\dist\installer
OutputBaseFilename=iPhotron-Setup-{#AppVersion}
SetupIconFile=app.ico
UninstallDisplayIcon={app}\{#AppExeName}
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=10.0

[Languages]
Name: "chinesesimplified"; MessagesFile: "compiler:Default.isl"
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "创建桌面快捷方式"; GroupDescription: "附加任务:"
Name: "quicklaunchicon"; Description: "创建快速启动快捷方式"; GroupDescription: "附加任务:"; Flags: unchecked

[Files]
; The whole Nuitka standalone output (exe + Qt + models + map data).
Source: "..\dist\iPhoto.dist\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\{#AppName}"; Filename: "{app}\{#AppExeName}"; IconFilename: "{app}\{#AppExeName}"
Name: "{group}\卸载 {#AppName}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\{#AppExeName}"; IconFilename: "{app}\{#AppExeName}"; Tasks: desktopicon
Name: "{userappdata}\Microsoft\Internet Explorer\Quick Launch\{#AppName}"; Filename: "{app}\{#AppExeName}"; Tasks: quicklaunchicon

[Run]
Filename: "{app}\{#AppExeName}"; Description: "立即启动 {#AppName}"; Flags: nowait postinstall skipifsilent
