import { launchElectron, paths } from "./electron-launcher.mjs";

launchElectron(paths.shellDir, {
  V8_REPO_ROOT: paths.repoRoot,
  V8_DESKTOP_PET_DIR: paths.desktopPetDir,
});
