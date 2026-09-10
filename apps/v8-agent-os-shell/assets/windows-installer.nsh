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

!define V8OS_SYSTEM_COMPONENT_CLEANUP "${__FILEDIR__}\windows-uninstall-system-components.ps1"
!macro customUnInstall
  # electron-builder invokes this before deleting the package. Its updater also
  # invokes the uninstaller; an upgrade must retain optional system components.
  ${IfNot} ${isUpdated}
    InitPluginsDir
    SetOutPath "$PLUGINSDIR"
    File /oname=v8os-remove-system-components.ps1 "${V8OS_SYSTEM_COMPONENT_CLEANUP}"
    StrCpy $1 "$SYSDIR\WindowsPowerShell\v1.0\powershell.exe"
    IfFileExists "$WINDIR\Sysnative\WindowsPowerShell\v1.0\powershell.exe" 0 +2
      StrCpy $1 "$WINDIR\Sysnative\WindowsPowerShell\v1.0\powershell.exe"
    nsExec::ExecToStack '"$1" -NoProfile -NonInteractive -WindowStyle Hidden -ExecutionPolicy Bypass -File "$PLUGINSDIR\v8os-remove-system-components.ps1" -EngineRoot "$INSTDIR\resources\v8os\apps\v8-agent-os-engine"'
    Pop $0
    Pop $1
    DetailPrint "$1"
    ${If} $0 != 0
      ${IfNot} ${Silent}
        MessageBox MB_OK|MB_ICONSTOP|MB_TOPMOST "V8OS system components could not be removed, or Windows elevation was cancelled.$\r$\nUninstall has stopped. Your application and credentials are preserved.$\r$\nIf a component is still in use, sign out or restart Windows and retry; otherwise repair or remove the components in Admin."
      ${EndIf}
      SetErrorLevel 1603
      Abort "V8OS optional system component cleanup was not confirmed."
    ${EndIf}
    SetOutPath "$INSTDIR"
  ${EndIf}
!macroend

!ifndef BUILD_UNINSTALLER
!define V8OS_GIT_X64_INSTALLER "${__FILEDIR__}\..\.release-prerequisites\Git-2.55.0.5-64-bit.exe"
!define V8OS_GIT_ARM64_INSTALLER "${__FILEDIR__}\..\.release-prerequisites\Git-2.55.0.5-arm64.exe"

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

  SetOutPath "$PLUGINSDIR"
  !ifdef APP_ARM64
    File /nonfatal /oname=v8os-git-prerequisite.exe "${V8OS_GIT_ARM64_INSTALLER}"
  !else
    File /nonfatal /oname=v8os-git-prerequisite.exe "${V8OS_GIT_X64_INSTALLER}"
  !endif
  IfFileExists "$PLUGINSDIR\v8os-git-prerequisite.exe" 0 v8os_git_try_winget
  DetailPrint "Git was not found. Installing the verified offline Git for Windows prerequisite..."
  nsExec::ExecToStack /TIMEOUT=120000 '"$PLUGINSDIR\v8os-git-prerequisite.exe" /SP- /VERYSILENT /SUPPRESSMSGBOXES /NORESTART /CURRENTUSER'
  Pop $2
  Pop $3
  StrCmp $2 "0" 0 v8os_git_bundled_install_failed
  Call V8OSGitIsInstalled
  Pop $0
  StrCmp $0 "1" v8os_git_install_complete v8os_git_bundled_install_failed

v8os_git_bundled_install_failed:
  DetailPrint "The bundled Git prerequisite did not complete (exit code: $2). Trying Windows Package Manager..."

v8os_git_try_winget:
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
