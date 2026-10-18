Unicode true
!include "MUI2.nsh"
!include "x64.nsh"
Name "杭电通知集 V${VERSION_NUMBER}"
OutFile "${OUTPUT_FILE}"
InstallDir "$LOCALAPPDATA\Programs\HDUNoticeHub"
InstallDirRegKey HKCU "Software\HDUNoticeHub" "InstallLocation"
RequestExecutionLevel user
SetCompressor /SOLID lzma
ShowInstDetails show
ShowUninstDetails show
VIProductVersion "${VERSION_NUMBER}.0"
VIAddVersionKey /LANG=2052 "ProductName" "杭电通知集"
VIAddVersionKey /LANG=2052 "FileDescription" "杭电通知集安装程序"
VIAddVersionKey /LANG=2052 "FileVersion" "${VERSION_NUMBER}"
VIAddVersionKey /LANG=2052 "LegalCopyright" "Copyright 2026 xiangyu-wang08"

!define MUI_ABORTWARNING
!define MUI_FINISHPAGE_RUN "$INSTDIR\杭电通知集.exe"
!define MUI_FINISHPAGE_RUN_TEXT "安装完成后启动杭电通知集"
!insertmacro MUI_PAGE_WELCOME
!insertmacro MUI_PAGE_LICENSE "${PROJECT_ROOT}\LICENSE"
!insertmacro MUI_PAGE_DIRECTORY
!insertmacro MUI_PAGE_INSTFILES
!insertmacro MUI_PAGE_FINISH
!insertmacro MUI_UNPAGE_CONFIRM
!insertmacro MUI_UNPAGE_INSTFILES
!insertmacro MUI_LANGUAGE "SimpChinese"

Function .onInit
  SetShellVarContext current
  SetRegView 64
  ${IfNot} ${RunningX64}
    MessageBox MB_OK|MB_ICONSTOP "此版本需要 Windows 10/11 x64。" /SD IDOK
    Abort
  ${EndIf}
FunctionEnd

Function un.onInit
  SetShellVarContext current
  SetRegView 64
  ReadINIStr $0 "$INSTDIR\installation.ini" "Application" "Id"
  StrCmp $0 "hdu-notice-hub" valid
    MessageBox MB_OK|MB_ICONSTOP "未找到本程序的安装标记，已停止卸载。" /SD IDOK
    Abort
  valid:
FunctionEnd

Section "安装"
  SetShellVarContext current
  SetRegView 64
  IfFileExists "$INSTDIR\installation.ini" check_marker check_files
  check_marker:
    ReadINIStr $0 "$INSTDIR\installation.ini" "Application" "Id"
    StrCmp $0 "hdu-notice-hub" stop_old invalid_dir
  check_files:
    FindFirst $1 $2 "$INSTDIR\*"
  next_file:
    StrCmp $2 "" checked_empty
    StrCmp $2 "." continue_file
    StrCmp $2 ".." continue_file
    StrCmp $2 "data" continue_file invalid_dir_close
  continue_file:
    FindNext $1 $2
    Goto next_file
  checked_empty:
    FindClose $1
    Goto stop_old
  invalid_dir_close:
    FindClose $1
  invalid_dir:
    MessageBox MB_OK|MB_ICONSTOP "请选择空文件夹，或本程序已有的安装目录。不会覆盖不属于本程序的文件。" /SD IDOK
    SetErrorLevel 2
    Abort
  stop_old:
    IfFileExists "$INSTDIR\杭电通知集.exe" 0 copy_files
    ExecWait '"$INSTDIR\杭电通知集.exe" --stop' $0
    StrCmp $0 0 copy_files
      MessageBox MB_OK|MB_ICONSTOP "请先关闭旧版服务，然后重新安装。" /SD IDOK
      SetErrorLevel 3
      Abort
  copy_files:
  !include "${GENERATED_DIR}\install-files.nsh"
  SetOutPath "$INSTDIR"
  WriteINIStr "$INSTDIR\installation.ini" "Application" "Id" "hdu-notice-hub"
  WriteINIStr "$INSTDIR\installation.ini" "Application" "Version" "V${VERSION_NUMBER}"
  WriteUninstaller "$INSTDIR\Uninstall.exe"
  CreateDirectory "$SMPROGRAMS\杭电通知集"
  CreateShortcut "$DESKTOP\杭电通知集.lnk" "$INSTDIR\杭电通知集.exe"
  CreateShortcut "$SMPROGRAMS\杭电通知集\杭电通知集.lnk" "$INSTDIR\杭电通知集.exe"
  CreateShortcut "$SMPROGRAMS\杭电通知集\关闭服务.lnk" "$INSTDIR\杭电通知集.exe" "--stop"
  CreateShortcut "$SMPROGRAMS\杭电通知集\安装与使用手册.lnk" "$INSTDIR\本地安装部署说明书.pdf"
  CreateShortcut "$SMPROGRAMS\杭电通知集\卸载.lnk" "$INSTDIR\Uninstall.exe"
  WriteRegStr HKCU "Software\HDUNoticeHub" "InstallLocation" "$INSTDIR"
  WriteRegStr HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\HDUNoticeHub" "DisplayName" "杭电通知集"
  WriteRegStr HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\HDUNoticeHub" "DisplayVersion" "${VERSION_NUMBER}"
  WriteRegStr HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\HDUNoticeHub" "Publisher" "xiangyu-wang08"
  WriteRegStr HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\HDUNoticeHub" "InstallLocation" "$INSTDIR"
  WriteRegStr HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\HDUNoticeHub" "UninstallString" '"$INSTDIR\Uninstall.exe"'
  WriteRegStr HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\HDUNoticeHub" "QuietUninstallString" '"$INSTDIR\Uninstall.exe" /S'
  WriteRegStr HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\HDUNoticeHub" "DisplayIcon" "$INSTDIR\杭电通知集.exe"
  WriteRegDWORD HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\HDUNoticeHub" "NoModify" 1
  WriteRegDWORD HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\HDUNoticeHub" "NoRepair" 1
SectionEnd

Section "Uninstall"
  IfFileExists "$INSTDIR\杭电通知集.exe" 0 remove_files
  ExecWait '"$INSTDIR\杭电通知集.exe" --stop' $0
  StrCmp $0 0 remove_files
    MessageBox MB_OK|MB_ICONSTOP "服务尚未关闭，请关闭后重新卸载。" /SD IDOK
    SetErrorLevel 3
    Abort
  remove_files:
  !include "${GENERATED_DIR}\uninstall-files.nsh"
  Delete "$INSTDIR\installation.ini"
  Delete "$INSTDIR\Uninstall.exe"
  ReadRegStr $0 HKCU "Software\HDUNoticeHub" "InstallLocation"
  StrCmp $0 "$INSTDIR" 0 keep_registration
    Delete "$DESKTOP\杭电通知集.lnk"
    Delete "$SMPROGRAMS\杭电通知集\杭电通知集.lnk"
    Delete "$SMPROGRAMS\杭电通知集\关闭服务.lnk"
    Delete "$SMPROGRAMS\杭电通知集\安装与使用手册.lnk"
    Delete "$SMPROGRAMS\杭电通知集\卸载.lnk"
    RMDir "$SMPROGRAMS\杭电通知集"
    DeleteRegKey HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\HDUNoticeHub"
    DeleteRegKey HKCU "Software\HDUNoticeHub"
  keep_registration:
  ; Intentionally non-recursive: preserve data and any unowned files.
  RMDir "$INSTDIR"
SectionEnd
