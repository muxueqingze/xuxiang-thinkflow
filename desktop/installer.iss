[Setup]
AppId=ThinkFlow.Desktop
AppName=续想 ThinkFlow
AppVersion={#AppVersion}
VersionInfoVersion={#AppVersion}
AppPublisher=ThinkFlow Contributors
DefaultDirName={localappdata}\Programs\ThinkFlow
DisableDirPage=no
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
MinVersion=10.0
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
UninstallDisplayIcon={app}\ThinkFlow.exe
Compression=lzma2/fast
SolidCompression=yes
WizardStyle=modern

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"
Name: "chinesesimplified"; MessagesFile: "compiler:Languages\ChineseSimplified.isl"

[Files]
Source: "{#AppSource}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{userprograms}\续想 ThinkFlow"; Filename: "{app}\ThinkFlow.exe"; WorkingDir: "{app}"
