; Installateur Windows de RT4D Programmer (Inno Setup 6).
; Installation par utilisateur, sans droits d'administrateur : la mise à jour
; automatique peut ainsi remplacer RT4D-Programmer.exe.
#ifndef MyVersion
  #define MyVersion "0.0.0"
#endif

[Setup]
AppId={{1D20C6F8-0CE7-4440-915E-37ECD35C09B7}
AppName=RT4D Programmer
AppVersion={#MyVersion}
AppPublisher=F4JCF
AppPublisherURL=https://github.com/F4JCF/rt4d-programmer
DefaultDirName={localappdata}\Programs\RT4D-Programmer
DefaultGroupName=RT4D Programmer
PrivilegesRequired=lowest
DisableProgramGroupPage=yes
OutputDir=dist
OutputBaseFilename=RT4D-Programmer-Setup-{#MyVersion}
SetupIconFile=icon.ico
UninstallDisplayIcon={app}\RT4D-Programmer.exe
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
CloseApplications=yes

[Languages]
Name: "fr"; MessagesFile: "compiler:Languages\French.isl"
Name: "en"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"

[Files]
Source: "dist\RT4D-Programmer.exe"; DestDir: "{app}"; Flags: ignoreversion

[Icons]
Name: "{group}\RT4D Programmer"; Filename: "{app}\RT4D-Programmer.exe"
Name: "{group}\{cm:UninstallProgram,RT4D Programmer}"; Filename: "{uninstallexe}"
Name: "{userdesktop}\RT4D Programmer"; Filename: "{app}\RT4D-Programmer.exe"; Tasks: desktopicon

[Run]
Filename: "{app}\RT4D-Programmer.exe"; Description: "{cm:LaunchProgram,RT4D Programmer}"; Flags: nowait postinstall skipifsilent
