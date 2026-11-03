; Recap Studio's installer (Inno Setup 6), compiled by packaging\build.ps1 from dist\Recap Studio:
;   ISCC /DAppVersion=1.0.0 /DEdition=lite /DOutputBase=Recap-Studio-Lite-1.0.0-setup "/DAppDir=<dist\Recap Studio>"
;        "/DOutputDir=<dist>" packaging\installer.iss
; Lite and Full Suite share its app id, folders and data, so installing one over the other switches edition and
; keeps every project and setting. Only the welcome page's "Recap Studio Lite 1.0.0" and the file's name differ.
; It installs into Program Files for everyone (admin rights), or, if you choose "for me only" at the start, into
; %LOCALAPPDATA%\Programs without them. The app never writes to its own folder: settings go in %APPDATA%,
; models and add-ons in %LOCALAPPDATA%, projects in Documents\Recap Studio (recap\paths.py), so updates and
; uninstalling never touch them. An update keeps the way it was installed (UsePreviousPrivileges).

#ifndef AppVersion
  #define AppVersion "0.0.0-dev"
#endif
#ifndef AppDir
  #define AppDir "..\dist\Recap Studio"
#endif
#ifndef OutputDir
  #define OutputDir "..\dist"
#endif
#ifndef Edition
  #define Edition "lite"
#endif
#if Edition == "full"
  #define EditionName "Full Suite"
  #define DefaultOutput "Recap-Studio-Full-Setup-" + AppVersion
#else
  #define EditionName "Lite"
  #define DefaultOutput "Recap-Studio-Lite-" + AppVersion + "-setup"
#endif
#ifndef OutputBase
  #define OutputBase DefaultOutput
#endif

[Setup]
AppId={{7EEC7A0E-13A0-4CC7-8AE4-EC5217C2A4C0}
AppName=Recap Studio
AppVersion={#AppVersion}
AppVerName=Recap Studio {#EditionName} {#AppVersion}
AppPublisher=Recap Studio
AppPublisherURL=https://github.com/iEatNoodlez/RecapStudio
AppSupportURL=https://github.com/iEatNoodlez/RecapStudio/issues
AppUpdatesURL=https://github.com/iEatNoodlez/RecapStudio/releases
DefaultDirName={autopf}\Recap Studio
DisableProgramGroupPage=yes
PrivilegesRequired=admin
PrivilegesRequiredOverridesAllowed=dialog commandline
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
OutputDir={#OutputDir}
OutputBaseFilename={#OutputBase}
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
; a clean copy of the program on every install (an update drops files a newer version no longer has)
Type: filesandordirs; Name: "{app}\python"
Type: filesandordirs; Name: "{app}\recap"
Type: filesandordirs; Name: "{app}\.claude\skills\recap-studio-narration"
; Full Suite's plugins: Lite installed over Full Suite takes them out, and Full Suite puts its own back (plugins
; installed from the list are in the data folder, and stay)
Type: filesandordirs; Name: "{app}\plugins"

[Files]
Source: "{#AppDir}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
; AppUserModelID: the id the app's window gives itself (recap\__main__.py APP_ID), so its taskbar button has the
; app's icon and pinning it pins this shortcut
Name: "{autoprograms}\Recap Studio"; Filename: "{app}\Recap Studio.exe"; AppUserModelID: "RecapStudio.App"
Name: "{autodesktop}\Recap Studio"; Filename: "{app}\Recap Studio.exe"; Tasks: desktopicon; AppUserModelID: "RecapStudio.App"

[Run]
; (as you, not as administrator, even after an install for everyone)
Filename: "{app}\Recap Studio.exe"; Description: "{cm:LaunchProgram,Recap Studio}"; Flags: nowait postinstall skipifsilent runasoriginaluser
; an update from inside the app runs this setup with /SILENT /update=1: start the app again afterwards
Filename: "{app}\Recap Studio.exe"; Flags: nowait runasoriginaluser; Check: IsUpdate

[UninstallDelete]
; what Python adds to the program folders; settings, models and projects are elsewhere and stay
Type: filesandordirs; Name: "{app}\python"
Type: filesandordirs; Name: "{app}\recap"
Type: filesandordirs; Name: "{app}\.claude"

[Code]
function IsUpdate: Boolean;
begin
  Result := ExpandConstant('{param:update|0}') = '1';
end;
