// Compile the actual panel and API adapter. Replace only native shell and session context.
const fs = require('node:fs');
const path = require('node:path');
const phone = path.resolve(__dirname, '..');
const output = path.resolve(process.argv[2]);
const admin = path.resolve(process.argv[3] || path.join(phone, '../v8-agent-os-admin'));
const mutant = process.argv[4] === 'stale-confirmation';
const webpack = require(path.join(admin, 'node_modules/next/dist/compiled/webpack/webpack.js')).webpack;
fs.mkdirSync(output, { recursive: true });
fs.writeFileSync(path.join(output, 'prefs.ts'), `
import { getThemeColors } from ${JSON.stringify(path.join(phone, 'src/theme/tokens.ts'))};
import { translateCurrent } from ${JSON.stringify(path.join(phone, 'src/lib/locale.ts'))};
const prefs = { colors: getThemeColors('light'), themeMode: 'light', t: translateCurrent };
export const useUiPrefs = () => prefs;
`);
fs.writeFileSync(path.join(output, 'session.ts'), `
import { useSyncExternalStore } from 'react';
const listeners = new Set();
const make = authority => ({ authorityKey: authority, servingInstanceId: authority,
  authorizedFetch: (path, init) => fetch('/fixture/' + authority + path, init) });
let session = make('A');
window.switchAuthority = authority => { session = make(authority); listeners.forEach(fn => fn()); };
export const useAppSession = () => useSyncExternalStore(fn => { listeners.add(fn); return () => listeners.delete(fn); }, () => session);
`);
fs.writeFileSync(path.join(output, 'visibility.ts'), `
import { useSyncExternalStore } from 'react';
const listeners = new Set();
let visible = true;
window.setForeground = value => { visible = value; listeners.forEach(fn => fn()); };
export const useAppVisibility = () => useSyncExternalStore(fn => { listeners.add(fn); return () => listeners.delete(fn); }, () => visible);
export const useIsFocused = () => true;
`);
fs.writeFileSync(path.join(output, 'safe-area.ts'), `export { View as SafeAreaView } from 'react-native';`);
fs.writeFileSync(path.join(output, 'entry.tsx'), `
import React from 'react';
import { createRoot } from 'react-dom/client';
import { ConfigDistributionPanel } from ${JSON.stringify(path.join(phone, 'src/components/connections/ConfigDistributionPanel.tsx'))};
const root = createRoot(document.getElementById('root'));
root.render(<ConfigDistributionPanel onClose={() => root.unmount()} />);
`);
fs.writeFileSync(path.join(output, 'ts-loader.cjs'), `
const ts = require(${JSON.stringify(require.resolve('typescript'))});
module.exports = function(source) {
  if (${JSON.stringify(mutant)} && this.resourcePath.endsWith('config-distribution.ts')) {
    source = source.replace('return \u0060\u0024{job.jobId}:\u0024{job.revision}:\u0024{job.planDigest}\u0060;', 'return job.jobId;');
  }
  return ts.transpileModule(source, {fileName:this.resourcePath,
  compilerOptions:{jsx:ts.JsxEmit.ReactJSX,target:ts.ScriptTarget.ES2022,module:ts.ModuleKind.ESNext,esModuleInterop:true}}).outputText; };
`);
fs.writeFileSync(path.join(output, 'index.html'), '<!doctype html><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><div id="root"></div><script src="fixture.js"></script>');
webpack({ mode:'development', devtool:false, context:phone, entry:path.join(output,'entry.tsx'), output:{path:output,filename:'fixture.js'},
    resolve:{extensions:['.tsx','.ts','.js'],modules:[path.join(phone,'node_modules'),'node_modules'],alias:{
        'react-native$':require.resolve('react-native-web'), '@/src/providers/ui-prefs$':path.join(output,'prefs.ts'),
        '@/src/providers/app-session$':path.join(output,'session.ts'), '@/src/hooks/use-app-visibility$':path.join(output,'visibility.ts'),
        '@react-navigation/native$':path.join(output,'visibility.ts'), 'react-native-safe-area-context$':path.join(output,'safe-area.ts'), '@':phone,
    }}, module:{rules:[{test:/\.tsx?$/,exclude:/node_modules/,use:path.join(output,'ts-loader.cjs')}]},
}, (error, stats) => {
    if (error || stats.hasErrors()) { console.error(error || stats.toString({all:false,errors:true})); process.exitCode=1; }
    else console.log('Configuration distribution UI built: ' + output);
});
