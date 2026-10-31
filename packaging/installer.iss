; Recap Studio's installer (Inno Setup 6), compiled by packaging\build.ps1 from dist\Recap Studio:
;   ISCC /DAppVersion=1.0.0 "/DAppDir=<dist\Recap Studio>" "/DOutputDir=<dist>" packaging\installer.iss
; It installs for the current user, with no admin rights, into %LOCALAPPDATA%\Programs\Recap Studio, where the app
; can keep its data and models folders next to itself as the portable copy does.

#ifndef AppVersion
  #define AppVersion "0.0.0-dev"
#endif
#ifndef AppDir
  #define AppDir "..\dist\Recap Studio"
#endif
#ifndef OutputDir
  #define OutputDir "..\dist"
#endif

[Setup]
AppId={{7EEC7A0E-13A0-4CC7-8AE4-EC5217C2A4C0}
AppName=Recap Studio
AppVersion={#AppVersion}
AppVerName=Recap Studio {#AppVersion}
AppPublisher=Recap Studio
AppPublisherURL=https://github.com/iEatNoodlez/RecapStudio
AppSupportURL=https://github.com/iEatNoodlez/RecapStudio/issues
AppUpdatesURL=https://github.com/iEatNoodlez/RecapStudio/releases
DefaultDirName={localappdata}\Programs\Recap Studio
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
OutputDir={#OutputDir}
OutputBaseFilename=Recap-Studio-{#AppVersion}-setup
SetupIconFile=launcher\icon.ico
UninstallDisplayIcon={app}\Recap Studio.exe
UninstallDisplayName=Recap Studio
LicenseFile=..\LICENSE
WizardStyle=modern
Compression=lzma2/max
SolidCompression=yes

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"; Flags: unchecked

[InstallDelete]
; a clean copy of the program on every install (an update drops files a newer version no longer has); the data
; and models folders are never touched
Type: filesandordirs; Name: "{app}\python"
Type: filesandordirs; Name: "{app}\recap"
Type: filesandordirs; Name: "{app}\.claude\skills\recap-studio-narration"

[Files]
Source: "{#AppDir}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{autoprograms}\Recap Studio"; Filename: "{app}\Recap Studio.exe"
Name: "{autodesktop}\Recap Studio"; Filename: "{app}\Recap Studio.exe"; Tasks: desktopicon

[Run]
Filename: "{app}\Recap Studio.exe"; Description: "{cm:LaunchProgram,Recap Studio}"; Flags: nowait postinstall skipifsilent
; an update from inside the app runs this setup with /SILENT /update=1: start the app again afterwards
Filename: "{app}\Recap Studio.exe"; Flags: nowait; Check: IsUpdate

[UninstallDelete]
; what the app compiles and adds to its own program folders; your projects (data) and models stay
Type: filesandordirs; Name: "{app}\python"
Type: filesandordirs; Name: "{app}\recap"
Type: filesandordirs; Name: "{app}\.claude"
Type: filesandordirs; Name: "{app}\data\updates"

[Code]
function IsUpdate: Boolean;
begin
  Result := ExpandConstant('{param:update|0}') = '1';
end;
