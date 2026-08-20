; Inno Setup スクリプト(実装計画 M7-2、設計書 §15)。
;
;     インストール先は既定 %LOCALAPPDATA%\Programs\ItemBank
;     (ユーザー単位、管理者権限不要)
;     ユーザーデータは %APPDATA%\ItemBank\(DB・バックアップ・テンプレート・取込原本)。
;     exe と同居させない
;
; 管理者権限を要求しない(PrivilegesRequired=lowest)。大学の共用 PC で管理者権限が
; 下りないことがあり、そこで詰まると配布そのものが止まる。
;
; ビルド:
;     iscc /DAppVersion=0.2.0 packaging\installer.iss
;
; **アンインストールでユーザーデータを消さない。** [UninstallDelete] に
; {userappdata}\ItemBank を書かないのはそのため。消したい人は手で消せる。

#define AppName "ItemBank"
#define AppPublisher "口腔組織学"
#define AppExeName "ItemBank.exe"

#ifndef AppVersion
  #define AppVersion "0.0.0"
#endif

#ifndef SourceDir
  #define SourceDir "..\dist\ItemBank"
#endif

[Setup]
; AppId は**変えない**。変えると上書きインストールが別アプリ扱いになり、
; 旧版が residue として残る。
AppId={{66064d2f-44c7-4e59-8875-745ca85d4b90}
AppName={#AppName}
AppVersion={#AppVersion}
AppVerName={#AppName} {#AppVersion}
AppPublisher={#AppPublisher}
VersionInfoVersion={#AppVersion}

DefaultDirName={localappdata}\Programs\{#AppName}
DefaultGroupName={#AppName}
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog

OutputDir=..\dist
OutputBaseFilename={#AppName}-{#AppVersion}-setup
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
ArchitecturesInstallIn64BitMode=x64compatible
; onedir なので中身が多い。展開の進捗を出す。
SetupLogging=yes

[Languages]
Name: "japanese"; MessagesFile: "compiler:Languages\Japanese.isl"

[Tasks]
Name: "desktopicon"; Description: "デスクトップにショートカットを作る"; GroupDescription: "追加のアイコン:"; Flags: unchecked

[Files]
; PyInstaller の onedir 出力をまるごと入れる。
Source: "{#SourceDir}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{userprograms}\{#AppName}"; Filename: "{app}\{#AppExeName}"
Name: "{userdesktop}\{#AppName}"; Filename: "{app}\{#AppExeName}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#AppExeName}"; Description: "{#AppName} を起動する"; Flags: nowait postinstall skipifsilent

[UninstallDelete]
; インストール先に残るキャッシュ類だけを消す。
; **{userappdata}\ItemBank は消さない**(DB・バックアップ・取込原本が入っている)。
Type: filesandordirs; Name: "{app}\_internal\__pycache__"

[Code]
function InitializeSetup(): Boolean;
begin
  Result := True;
end;

procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
begin
  if CurUninstallStep = usPostUninstall then
    MsgBox('問題バンクのデータは ' + ExpandConstant('{userappdata}\ItemBank') +
           ' に残しました。' + #13#10 +
           '不要であれば手動で削除してください。', mbInformation, MB_OK);
end;
