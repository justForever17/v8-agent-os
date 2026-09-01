!macro customInit
  # A single-architecture ARM64 installer otherwise falls through electron-builder's
  # package selection on x64 Windows and can leave only the uninstaller behind.
  !ifdef APP_ARM64
    !ifndef APP_64
      ${IfNot} ${IsNativeARM64}
        ${IfNot} ${Silent}
          MessageBox MB_OK|MB_ICONSTOP|MB_TOPMOST "This installer requires Windows on ARM64.$\r$\nPlease download the Windows x64 installer for this computer."
        ${EndIf}
        ; NSIS may create an empty target before customInit runs. Remove only
        ; that empty directory; RMDir never deletes a previous installation.
        RMDir "$INSTDIR"
        SetErrorLevel 1633
        Quit
      ${EndIf}
    !endif
  !endif
!macroend

!ifndef BUILD_UNINSTALLER
Function V8OSGitIsInstalled
  nsExec::ExecToStack /TIMEOUT=10000 '"$SYSDIR\where.exe" git.exe'
  Pop $0
  Pop $1
  StrCmp $0 "0" v8os_git_found
  IfFileExists "$LOCALAPPDATA\Programs\Git\cmd\git.exe" v8os_git_found
  IfFileExists "$PROGRAMFILES\Git\cmd\git.exe" v8os_git_found
  IfFileExists "$PROGRAMFILES64\Git\cmd\git.exe" v8os_git_found
  IfFileExists "$PROGRAMFILES32\Git\cmd\git.exe" v8os_git_found
  Push "0"
  Return
v8os_git_found:
  Push "1"
FunctionEnd

Function V8OSResolveWinGet
  IfFileExists "$LOCALAPPDATA\Microsoft\WindowsApps\winget.exe" v8os_winget_alias
  nsExec::ExecToStack /TIMEOUT=10000 '"$SYSDIR\where.exe" winget.exe'
  Pop $0
  Pop $1
  StrCmp $0 "0" v8os_winget_path
  Push ""
  Return
v8os_winget_alias:
  Push "$LOCALAPPDATA\Microsoft\WindowsApps\winget.exe"
  Return
v8os_winget_path:
  Push "winget.exe"
FunctionEnd

!macro customInstall
  Call V8OSGitIsInstalled
  Pop $0
  StrCmp $0 "1" v8os_git_already_installed

  Call V8OSResolveWinGet
  Pop $1
  StrCmp $1 "" v8os_git_install_unavailable
  DetailPrint "Git was not found. Installing the official Git.Git package for the current user..."
  nsExec::ExecToStack /TIMEOUT=120000 '"$1" install --id Git.Git --exact --source winget --scope user --silent --accept-package-agreements --accept-source-agreements --disable-interactivity --no-upgrade'
  Pop $2
  Pop $3
  StrCmp $2 "0" 0 v8os_git_install_failed
  Call V8OSGitIsInstalled
  Pop $0
  StrCmp $0 "1" v8os_git_install_complete v8os_git_install_failed

v8os_git_already_installed:
  DetailPrint "Git is already installed; prerequisite installation was skipped."
  Goto v8os_git_install_done
v8os_git_install_complete:
  DetailPrint "Git prerequisite installation completed."
  Goto v8os_git_install_done
v8os_git_install_unavailable:
  DetailPrint "Git is missing and WinGet is unavailable. V8OS will continue without optional Git parallel isolation."
  ${IfNot} ${Silent}
    MessageBox MB_OK|MB_ICONEXCLAMATION|MB_TOPMOST "Git is not installed and Windows Package Manager is unavailable.$\r$\nV8 Agent OS will finish installing, but optional Git parallel isolation will remain unavailable."
  ${EndIf}
  Goto v8os_git_install_done
v8os_git_install_failed:
  DetailPrint "Git installation did not complete (WinGet exit code: $2). V8OS will continue without optional Git parallel isolation."
  ${IfNot} ${Silent}
    MessageBox MB_OK|MB_ICONEXCLAMATION|MB_TOPMOST "Git could not be installed automatically.$\r$\nV8 Agent OS will finish installing, but optional Git parallel isolation will remain unavailable."
  ${EndIf}
v8os_git_install_done:
!macroend
!endif
