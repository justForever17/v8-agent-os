import assert from 'node:assert/strict';
import fs from 'node:fs';
import vm from 'node:vm';
import test from 'node:test';
import ts from 'typescript';
import * as bootstrap from '../dist/product-theme-bootstrap.js';

test('resident activation reads canonical theme once, ignores hidden events and releases its listener', async () => {
  const cleanups = [], listeners = new Set(), themes = [];
  const window = new EventTarget(), document = new EventTarget();
  document.visibilityState = 'visible';
  window.v8osShell = { onSurfaceVisibilityChange(callback) {
    listeners.add(callback); callback({ visible:false });
    return () => listeners.delete(callback);
  } };
  let reads = 0, finish;
  const jsx = (type, props) => ({type, props});
  const react = { createContext:()=>({Provider:'provider'}), useContext:()=>null,
    useRef:current=>({current}), useState:initial=>[initial,()=>{}],
    useCallback:callback=>callback, useMemo:callback=>callback(), useEffect:callback=>cleanups.push(callback()) };
  const module = {exports:{}};
  const code = ts.transpileModule(fs.readFileSync(new URL('../src/ProductTheme.tsx',import.meta.url),'utf8'),
    {compilerOptions:{module:ts.ModuleKind.CommonJS,jsx:ts.JsxEmit.ReactJSX,target:ts.ScriptTarget.ES2022}}).outputText;
  const mocks = {react,'react/jsx-runtime':{jsx,jsxs:jsx},
    'next-themes':{ThemeProvider:'next-provider',useTheme:()=>({theme:'light',resolvedTheme:'light',setTheme:t=>themes.push(t)})},
    './product-theme-bootstrap.js':bootstrap};
  vm.runInNewContext(`(function(require,module,exports){${code}\n})`,{window,document,console,
    localStorage:{setItem(){}},fetch:()=>{reads++;return new Promise(resolve=>{finish=()=>resolve({ok:true,status:200,json:async()=>({theme:'dark'})})})}})
    (name=>mocks[name],module,module.exports);
  const tree = module.exports.ProductThemeProvider({canonicalTheme:'light',initialSyncState:'synced',children:null});
  tree.props.children.type(tree.props.children.props);
  assert.equal(listeners.size,1);
  assert.equal(reads,0);
  for(const cb of listeners){cb({visible:false});cb({visible:true});cb({visible:true});}
  window.dispatchEvent(new Event('focus'));
  document.dispatchEvent(new Event('visibilitychange'));
  assert.equal(reads,1,'activation/focus/visibility share the existing in-flight read');
  finish(); await new Promise(resolve=>setImmediate(resolve));
  assert.equal(themes.at(-1),'dark');
  for(const cb of listeners)cb({visible:false});
  assert.equal(reads,1,'hidden surface never requests a refresh');
  for(const cleanup of cleanups)cleanup?.();
  assert.equal(listeners.size,0);
  window.dispatchEvent(new Event('focus'));
  assert.equal(reads,1,'unmounted provider has no listeners');
});
