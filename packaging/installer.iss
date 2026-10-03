; Compile with Inno Setup 6 (ISCC.exe packaging\installer.iss) after running packaging\build_dist.py
#define AppName "YT字幕機"
#define AppVersion "1.0.0"

[Setup]
AppId={{B7C1E5A2-3D4F-4A8B-9E21-5F6A7C8D9E01}
AppName={#AppName}
AppVersion={#AppVersion}
DefaultDirName={localappdata}\Programs\{#AppName}
DefaultGroupName={#AppName}
PrivilegesRequired=lowest
OutputDir=output
OutputBaseFilename=YT字幕機_安裝程式_v{#AppVersion}
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
DisableProgramGroupPage=yes
UninstallDisplayName={#AppName}
InfoBeforeFile=install_notice.txt

[Tasks]
Name: "desktopicon"; Description: "建立桌面捷徑"; Flags: checkedonce

[Files]
Source: "{#BuildDir}\*"; DestDir: "{app}"; Flags: recursesubdirs createallsubdirs ignoreversion

[Icons]
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\啟動字幕機.bat"; WorkingDir: "{app}"; Tasks: desktopicon
Name: "{group}\{#AppName}"; Filename: "{app}\啟動字幕機.bat"; WorkingDir: "{app}"
Name: "{group}\使用說明"; Filename: "{app}\使用說明.html"
Name: "{autodesktop}\{#AppName} 使用說明"; Filename: "{app}\使用說明.html"; Tasks: desktopicon

[Run]
Filename: "{app}\啟動字幕機.bat"; Description: "立即啟動 {#AppName}"; WorkingDir: "{app}"; Flags: postinstall nowait skipifsilent

[UninstallDelete]
Type: filesandordirs; Name: "{app}\python"
