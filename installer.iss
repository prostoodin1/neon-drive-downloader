#ifndef MyAppVersion
  #define MyAppVersion "5.4.0-beta.4"
#endif

#ifndef MyAppFileVersion
  #define MyAppFileVersion "5.4.0.4"
#endif

#define MyAppName "Neon Drive Downloader"
#define MyAppExeName "NeonDriveDownloader.exe"

[Setup]
AppId={{E6B76B7F-32F0-4C41-89B1-5A1694D1C7E4}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher=NeonTools
AppPublisherURL=https://github.com/prostoodin1/neon-drive-downloader
AppSupportURL=https://github.com/prostoodin1/neon-drive-downloader/issues
AppUpdatesURL=https://github.com/prostoodin1/neon-drive-downloader/releases
DefaultDirName={localappdata}\Programs\{#MyAppName}
DefaultGroupName={#MyAppName}
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
OutputDir=dist
OutputBaseFilename=NeonDriveDownloader-Setup
SetupIconFile=assets\neon-drive-v2.ico
UninstallDisplayIcon={app}\{#MyAppExeName}
Compression=lzma2/ultra64
SolidCompression=yes
WizardStyle=modern
CloseApplications=yes
RestartApplications=yes
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
VersionInfoVersion={#MyAppFileVersion}
VersionInfoProductName={#MyAppName}
VersionInfoDescription=Reliable Google Drive background downloader

[Languages]
Name: "russian"; MessagesFile: "compiler:Languages\Russian.isl"

[Tasks]
Name: "desktopicon"; Description: "Создать ярлык на рабочем столе"; GroupDescription: "Дополнительные ярлыки:"; Flags: unchecked

[Files]
Source: "dist\NeonDriveDownloader\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{autoprograms}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "Запустить {#MyAppName}"; Flags: nowait postinstall skipifsilent
