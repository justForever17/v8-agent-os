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
