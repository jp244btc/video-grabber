; Inno Setup script for Video Grabber.
;
; Built by build\build.ps1, which passes the version in:
;   ISCC.exe /DAppVersion=1.2.3 installer\VideoGrabber.iss
;
; Installs per-user (no admin prompt), offers a hidden-at-sign-in autostart,
; downloads ffmpeg with a progress bar, and copies the browser extension
; folder alongside the app so "Load unpacked" has something to point at.

#ifndef AppVersion
  #define AppVersion "0.0.0"
#endif

[Setup]
AppId={{7E1D9E3A-6C1B-4D77-9C7B-6F3F1B2D5A10}
AppName=Video Grabber
AppVersion={#AppVersion}
AppVerName=Video Grabber {#AppVersion}
AppPublisher=Video Grabber contributors
AppPublisherURL=https://github.com/jp244btc/video-grabber
AppSupportURL=https://github.com/jp244btc/video-grabber/issues
DefaultDirName={localappdata}\Programs\Video Grabber
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
OutputDir=..\dist
OutputBaseFilename=VideoGrabber-Setup-{#AppVersion}
SetupIconFile=..\extension\icons\icon.ico
UninstallDisplayIcon={app}\VideoGrabber.exe
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
CloseApplications=yes
RestartApplications=no
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "startup"; Description: "Start Video Grabber hidden when I sign in (recommended)"; GroupDescription: "Startup:"
Name: "ffmpeg"; Description: "Download ffmpeg now (about 100 MB, needed for most downloads)"; GroupDescription: "Components:"

[Files]
Source: "..\dist\VideoGrabber\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "..\extension\*"; DestDir: "{app}\extension"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "..\README.md"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\LICENSE"; DestDir: "{app}"; Flags: ignoreversion

[Icons]
Name: "{autoprograms}\Video Grabber"; Filename: "{app}\VideoGrabber.exe"; Comment: "Open the Video Grabber page"
Name: "{autoprograms}\Video Grabber extension folder"; Filename: "{app}\extension"; Comment: "Load this folder unpacked in your browser"
Name: "{userstartup}\Video Grabber"; Filename: "{app}\VideoGrabber.exe"; Parameters: "--no-browser"; Tasks: startup

[Run]
Filename: "{app}\VideoGrabber.exe"; Parameters: "--install-ffmpeg ""{tmp}\ffmpeg.zip"""; StatusMsg: "Unpacking ffmpeg..."; Tasks: ffmpeg; Flags: runhidden waituntilterminated
Filename: "{app}\VideoGrabber.exe"; Description: "Start Video Grabber now"; Flags: postinstall nowait skipifsilent

[UninstallDelete]
Type: filesandordirs; Name: "{localappdata}\Video Grabber"

[Code]
var
  DownloadPage: TDownloadWizardPage;

procedure StopRunningCopy;
var
  ResultCode: Integer;
begin
  { The app usually runs hidden with no window to close. }
  Exec(ExpandConstant('{sys}\taskkill.exe'), '/F /IM VideoGrabber.exe', '',
       SW_HIDE, ewWaitUntilTerminated, ResultCode);
end;

function OnDownloadProgress(const Url, FileName: String; const Progress, ProgressMax: Int64): Boolean;
begin
  Result := True;
end;

procedure InitializeWizard;
begin
  DownloadPage := CreateDownloadPage(
    'Downloading ffmpeg',
    'Fetching the static ffmpeg build published by the yt-dlp project.',
    @OnDownloadProgress);
end;

function PrepareToInstall(var NeedsRestart: Boolean): String;
begin
  StopRunningCopy;
  Result := '';
end;

function NextButtonClick(CurPageID: Integer): Boolean;
begin
  Result := True;
  if (CurPageID = wpReady) and WizardIsTaskSelected('ffmpeg') then begin
    DownloadPage.Clear;
    DownloadPage.Add(
      'https://github.com/yt-dlp/FFmpeg-Builds/releases/latest/download/ffmpeg-master-latest-win64-gpl.zip',
      'ffmpeg.zip', '');
    DownloadPage.Show;
    try
      try
        DownloadPage.Download;
        Result := True;
      except
        if DownloadPage.AbortedByUser then
          Log('ffmpeg download aborted by user.')
        else
          SuppressibleMsgBox(
            'ffmpeg could not be downloaded: ' + AddPeriod(GetExceptionMessage) + #13#10 +
            'Setup will continue. You can install ffmpeg later from the app page.',
            mbInformation, MB_OK, IDOK);
        { Carry on without it; the app offers the same download from its page. }
        Result := True;
      end;
    finally
      DownloadPage.Hide;
    end;
  end;
end;

function InitializeUninstall: Boolean;
begin
  StopRunningCopy;
  Result := True;
end;
