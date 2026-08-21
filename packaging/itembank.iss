; Inno Setup スクリプト(実装計画 §8 / M7-2)。
;
;   ISCC.exe /DAppVersion=0.3.0 /DNumericVersion=0.3.0.0 packaging\itembank.iss
;
; 出来上がるのは dist\installer\ItemBank-0.3.0-setup.exe。
; **ファイル名にバージョンが入る**ので、Release に並べたときにどれが何版か分かる。
;
; 方針(実装計画 §8 / 設計書 §15):
;   * ユーザー単位インストール。管理者権限を要求しない
;     (%LOCALAPPDATA%\Programs\ItemBank。exe の置き場は書込不可を前提に扱う)
;   * **ユーザーデータ(%APPDATA%\ItemBank)には一切触らない**。上書きインストール
;     でも、アンインストールでも DB・バックアップ・取込原本は残る

#ifndef AppVersion
  #define AppVersion "0.0.0-dev"
#endif
#ifndef NumericVersion
  #define NumericVersion "0.0.0.0"
#endif

#define AppName "ItemBank"
#define AppExeName "ItemBank.exe"
#define AppDisplayName "ItemBank(試験問題バンク)"

[Setup]
; AppId はアプリの同一性そのもの。**版を上げても変えない**
; (変えると別アプリ扱いになり、上書きではなく二重インストールになる)。
AppId={{6E2B5C7A-1F94-4C8E-9E1B-3A5D0C7F2B41}
AppName={#AppDisplayName}
AppVersion={#AppVersion}
AppVerName={#AppDisplayName} {#AppVersion}
VersionInfoVersion={#NumericVersion}
VersionInfoProductVersion={#NumericVersion}
AppPublisher={#AppName}
DefaultDirName={localappdata}\Programs\{#AppName}
DefaultGroupName={#AppDisplayName}
DisableProgramGroupPage=yes
UninstallDisplayName={#AppDisplayName} {#AppVersion}
UninstallDisplayIcon={app}\{#AppExeName}
OutputDir=..\dist\installer
OutputBaseFilename={#AppName}-{#AppVersion}-setup
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
; 管理者権限を求めない。求めると学内 PC で入れられないことがある。
PrivilegesRequired=lowest
ArchitecturesAllowed=x64
ArchitecturesInstallIn64BitMode=x64
; PySide6 の Qt が 64bit 版しか無いため、32bit Windows は対象外にする。
MinVersion=10.0

[Languages]
Name: "japanese"; MessagesFile: "compiler:Languages\Japanese.isl"
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"; Flags: unchecked

[Files]
; PyInstaller の onedir 出力を丸ごと入れる。
Source: "..\dist\{#AppName}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{autoprograms}\{#AppDisplayName}"; Filename: "{app}\{#AppExeName}"
Name: "{autodesktop}\{#AppDisplayName}"; Filename: "{app}\{#AppExeName}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#AppExeName}"; Description: "{cm:LaunchProgram,{#AppDisplayName}}"; Flags: nowait postinstall skipifsilent

; [UninstallDelete] は置かない。{app} 配下しか消さないので、
; %APPDATA%\ItemBank のデータは上書きインストールでもアンインストールでも残る
; (実装計画 §4 M7 受入条件「上書きインストールしてもデータが保持され、移行が走る」)。
