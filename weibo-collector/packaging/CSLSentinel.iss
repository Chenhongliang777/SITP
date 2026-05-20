; Inno Setup 脚本 — 需安装 Inno Setup 6
; 在 weibo-collector 目录执行: iscc packaging\CSLSentinel.iss

#define MyAppName "CSL Sentinel 足球舆情监测"
#define MyAppVersion "1.0.0"
#define MyAppPublisher "CSL Sentinel"
#define MyAppExeName "CSLSentinel.exe"

[Setup]
AppId={{A8F3C2E1-9B4D-4F6A-8C1E-2D5B7A9E0F31}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
DefaultDirName={autopf}\CSLSentinel
DefaultGroupName={#MyAppName}
DisableProgramGroupPage=yes
OutputDir=..\dist
OutputBaseFilename=CSLSentinel_Setup
Compression=lzma2/ultra64
SolidCompression=yes
WizardStyle=modern
ArchitecturesInstallIn64BitMode=x64compatible
PrivilegesRequired=admin

; 若已安装中文语言包，可取消下行注释：
; [Languages]
; Name: "chinesesimplified"; MessagesFile: "compiler:Languages\ChineseSimplified.isl"

[Tasks]
Name: "desktopicon"; Description: "创建桌面快捷方式"; GroupDescription: "附加图标:"

[Files]
Source: "..\dist\CSLSentinel\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"
Name: "{group}\使用说明"; Filename: "{app}\使用说明.txt"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "启动 {#MyAppName}"; Flags: nowait postinstall skipifsilent

[UninstallDelete]
Type: filesandordirs; Name: "{app}\data"
Type: filesandordirs; Name: "{app}\reports"
Type: filesandordirs; Name: "{app}\logs"
