; Installatore di StillFind per Windows (Inno Setup 6). Installa per l'utente corrente, senza amministratore.
; Uso:  iscc /DAppVersion=0.1.0 /DSourceDir=dist\StillFind /DIconFile=build\icons\stillfind.ico packaging\windows\stillfind.iss
#ifndef AppVersion
  #define AppVersion "0.0.0"
#endif
#ifndef SourceDir
  #define SourceDir "..\..\dist\StillFind"
#endif

[Setup]
AppId={{6E0B9C3A-5E8B-4C55-9A64-3C5A2B1D7F10}
AppName=StillFind (alpha)
AppVersion={#AppVersion}
AppPublisher=Daniele Sisto
DefaultDirName={autopf}\StillFind
DefaultGroupName=StillFind
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog
InfoBeforeFile=..\ALPHA.txt
LicenseFile=..\..\LICENSE
OutputDir=..\..\dist
OutputBaseFilename=StillFind-windows-setup
Compression=lzma2
SolidCompression=yes
ArchitecturesInstallIn64BitMode=x64compatible
WizardStyle=modern
#ifdef IconFile
SetupIconFile={#IconFile}
#endif
UninstallDisplayIcon={app}\StillFind.exe

[Languages]
Name: "italian"; MessagesFile: "compiler:Languages\Italian.isl"
Name: "english"; MessagesFile: "compiler:Default.isl"
Name: "french"; MessagesFile: "compiler:Languages\French.isl"
Name: "spanish"; MessagesFile: "compiler:Languages\Spanish.isl"
Name: "german"; MessagesFile: "compiler:Languages\German.isl"
Name: "brazilianportuguese"; MessagesFile: "compiler:Languages\BrazilianPortuguese.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; Flags: unchecked

[Files]
Source: "{#SourceDir}\*"; DestDir: "{app}"; Flags: recursesubdirs createallsubdirs ignoreversion

[Icons]
Name: "{group}\StillFind"; Filename: "{app}\StillFind.exe"
Name: "{group}\{cm:UninstallProgram,StillFind}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\StillFind"; Filename: "{app}\StillFind.exe"; Tasks: desktopicon

[Run]
Filename: "{app}\StillFind.exe"; Description: "{cm:LaunchProgram,StillFind}"; Flags: nowait postinstall skipifsilent
