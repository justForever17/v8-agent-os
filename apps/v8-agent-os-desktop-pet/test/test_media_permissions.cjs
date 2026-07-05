const { app, BrowserWindow, session } = require('electron');
const path = require('path');

app.whenReady().then(() => {
  console.log('====================================================');
  console.log('       CyberCore Electron Media Permission Test      ');
  console.log('====================================================');
  console.log(`OS Platform: ${process.platform}`);
  console.log(`Electron Version: ${process.versions.electron}`);
  console.log(`Chrome Version: ${process.versions.chrome}`);
  console.log('----------------------------------------------------');

  // Create a headless-ish window just to run the getUserMedia JS
  const win = new BrowserWindow({
    width: 600,
    height: 400,
    show: false, // Keep hidden or show it for user visibility
    webPreferences: {
      nodeIntegration: true,
      contextIsolation: false, // disable to easily communicate test results
    }
  });

  // Track permission requests
  session.defaultSession.setPermissionRequestHandler((webContents, permission, callback, details) => {
    const url = webContents.getURL();
    console.log(`[Main Process] Permission Request:`);
    console.log(`  - Permission: "${permission}"`);
    console.log(`  - URL: "${url}"`);
    console.log(`  - Details: ${JSON.stringify(details || {})}`);
    
    // Automatically approve for this test
    console.log(`  - Action: APPROVING request`);
    callback(true);
  });

  session.defaultSession.setPermissionCheckHandler((webContents, permission, requestingOrigin, details) => {
    const url = webContents.getURL();
    console.log(`[Main Process] Permission Check:`);
    console.log(`  - Permission: "${permission}"`);
    console.log(`  - URL: "${url}"`);
    console.log(`  - Origin: "${requestingOrigin}"`);
    
    // Automatically approve for this test
    return true;
  });

  // Listen for console messages from renderer
  win.webContents.on('console-message', (event, level, message) => {
    console.log(`[Renderer Process] ${message}`);
    
    if (message.startsWith('TEST_FINISHED:')) {
      const success = message.includes('SUCCESS');
      console.log('----------------------------------------------------');
      console.log(`Test Result: ${success ? '\x1b[32mPASSED\x1b[0m' : '\x1b[31mFAILED\x1b[0m'}`);
      console.log('====================================================');
      app.quit();
    }
  });

  win.loadFile(path.join(__dirname, 'test_page.html'));
});
