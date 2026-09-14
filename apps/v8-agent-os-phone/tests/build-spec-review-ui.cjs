// Actual React Native Web review, API, transport, draft store and locale modules.
// Substitute only decoration/theme and the native metadata persistence adapter.
const fs = require('node:fs');
const path = require('node:path');
const phone = path.resolve(__dirname, '..');
const output = path.resolve(process.argv[2]);
const webpack = require(path.resolve(phone, '../v8-agent-os-admin/node_modules/next/dist/compiled/webpack/webpack.js')).webpack;
fs.mkdirSync(output, { recursive: true });
fs.writeFileSync(path.join(output, 'prefs.ts'), `
import { getThemeColors } from ${JSON.stringify(path.join(phone, 'src/theme/tokens.ts'))};
import { translateCurrent } from ${JSON.stringify(path.join(phone, 'src/lib/locale.ts'))};
const prefs = { colors: getThemeColors('light'), themeMode: 'light', t: translateCurrent };
export const useUiPrefs = () => prefs;
`);
fs.writeFileSync(path.join(output, 'storage.ts'), `
export const readMetadata = async key => localStorage.getItem(key);
export const writeMetadata = async (key, value) => { localStorage.setItem(key, value); };
`);
fs.writeFileSync(path.join(output, 'expo-fetch.ts'), 'export const fetch = (...args) => globalThis.fetch(...args);');
fs.writeFileSync(path.join(output, 'entry.tsx'), `
import React from 'react';
import { createRoot } from 'react-dom/client';
import { SpecApprovalReview } from ${JSON.stringify(path.join(phone, 'src/components/chat/SpecApprovalReview.tsx'))};
import { PhoneTransport } from ${JSON.stringify(path.join(phone, 'src/lib/phone-transport.ts'))};
const root = createRoot(document.getElementById('root'));
let transport;
window.showAuthority = (authority = 'A') => {
  transport?.dispose();
  transport = new PhoneTransport({ endpoints: [location.origin], instanceId: 'fixture-instance',
    principalId: 'fixture-owner', native: false, credentials: { accessToken: 'synthetic-access', refreshToken: 'synthetic-refresh' },
    persistRefresh: async () => {}, onEndpoint() {}, onClock() {} });
  const activeTransport = transport;
  root.render(<SpecApprovalReview key={authority} authorityKey={authority} approvalId='old-card'
    workspacePath='fixture-workspace' specId='feature-1' stage='requirements'
    authorizedFetch={(...args) => activeTransport.authorizedFetch(...args)} />);
};
window.showAuthority();
`);
fs.writeFileSync(path.join(output, 'ts-loader.cjs'), `
const ts = require(${JSON.stringify(require.resolve('typescript'))});
module.exports = function(source) { return ts.transpileModule(source, {fileName:this.resourcePath,
  compilerOptions:{jsx:ts.JsxEmit.ReactJSX,target:ts.ScriptTarget.ES2022,module:ts.ModuleKind.ESNext,esModuleInterop:true}}).outputText; };
`);
fs.writeFileSync(path.join(output, 'index.html'), '<!doctype html><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><div id="root"></div><script src="fixture.js"></script>');
webpack({ mode:'development', devtool:false, context:phone, entry:path.join(output,'entry.tsx'),
  output:{path:output,filename:'fixture.js'},
  resolve:{extensions:['.tsx','.ts','.js'],modules:[path.join(phone,'node_modules'),'node_modules'],alias:{
    'react-native$':require.resolve('react-native-web'), '@/src/providers/ui-prefs$':path.join(output,'prefs.ts'),
    '@/src/lib/mobile-storage$':path.join(output,'storage.ts'), 'expo/fetch$':path.join(output,'expo-fetch.ts'), '@':phone,
  }}, module:{rules:[{test:/\.tsx?$/,exclude:/node_modules/,use:path.join(output,'ts-loader.cjs')}]},
}, (error, stats) => {
  if(error || stats.hasErrors()) { console.error(error || stats.toString({all:false,errors:true})); process.exitCode=1; }
  else console.log('Spec review UI built: '+output);
});
