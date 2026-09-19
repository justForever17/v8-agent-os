// Real React Native Web navigation fixture. Network, persistence, native hardware,
// animation and the Expo router are explicit boundaries; production menus are real.
// node tests/build-phone-navigation-ui.cjs <output> <admin-dir> [baseline-ref]
const fs = require("node:fs");
const path = require("node:path");
const { createHash } = require("node:crypto");
const { execFileSync } = require("node:child_process");
const phone = path.resolve(__dirname, "..");
const repo = path.resolve(phone, "../..");
const output = path.resolve(process.argv[2]);
const admin = path.resolve(process.argv[3] || path.join(phone, "../v8-agent-os-admin"));
const baseline = process.argv[4];
const webpack = require(path.join(admin, "node_modules/next/dist/compiled/webpack/webpack.js")).webpack;
fs.mkdirSync(output, { recursive: true });
const write = (name, value) => { const target = path.join(output, name); fs.mkdirSync(path.dirname(target), { recursive: true }); fs.writeFileSync(target, value); return target; };
const snapshots = [
    "src/components/layout/PhoneTopbar.tsx", "src/components/layout/LocaleMenu.tsx",
    "src/components/rpa/PhoneRpaOverlay.tsx", "src/components/rpa/RpaPanelContent.tsx",
    "src/components/chat/ProfileMenuOverlay.tsx", "src/components/ui/dropdown-menu.tsx",
    "src/components/ui/AvatarCropModal.tsx", "src/screens/SettingsScreen.tsx",
    "src/screens/SessionsScreen.tsx", "src/screens/ConnectScreen.tsx", "src/screens/RPAScreen.tsx",
    "src/components/common/LoadingScreen.tsx", "src/components/common/GlassCard.tsx",
    "src/theme/tokens.ts", "src/i18n/locales/en.json", "src/i18n/locales/zh-CN.json",
    "assets/images/product-mark.png",
];
const frozenAliases = {};
if (baseline) for (const file of snapshots) {
    const bytes = execFileSync("git", ["show", `${baseline}:apps/v8-agent-os-phone/${file}`], { cwd: repo, maxBuffer: 8 * 1024 * 1024 });
    const target = write(`baseline/${file}`, bytes);
    if (file.startsWith("src/")) frozenAliases[`@/${file.replace(/\.tsx?$/, "")}$`] = target;
}
write("fixture-state.ts", `
import { useSyncExternalStore } from 'react';
import { getThemeColors } from '@/src/theme/tokens';
import en from '@/src/i18n/locales/en.json';
import zh from '@/src/i18n/locales/zh-CN.json';
const query = new URLSearchParams(location.search);
const listeners = new Set<() => void>();
export let state: any = { page:query.get('page') || 'chat', locale:query.get('locale') || 'zh-CN', themeMode:query.get('theme') || 'light',
  fontScale:Number(query.get('fontScale') || 1), focused:true, visible:true, authorityKey:'A', servingInstanceId:'engine-A', status:'authenticated',
  insets:{top:Number(query.get('top') || 0),right:Number(query.get('right') || 0),bottom:Number(query.get('bottom') || 0),left:Number(query.get('left') || 0)},
  desktopDisabled:false, profileOpen:false, draftMode:'immediate', draft:'未发送的中文草稿 · Unsent draft', longNames:true };
export const records: any = { routes:[], flushCalls:0, pendingFlushes:[], api:[], desktop:0, profile:0 };
export function patch(next: any) { state = {...state,...next}; listeners.forEach(fn => fn()); }
export function useFixture() { return useSyncExternalStore(fn => {listeners.add(fn); return () => listeners.delete(fn);}, () => state); }
const tFor = (locale: string) => (key: string, params: any = {}) => {
 const message = (locale === 'en' ? en : zh)[key] || zh[key] || key;
 return message.replace(/\\{([^}]+)\\}/g, (_: string, name: string) => String(params[name] ?? '{'+name+'}'));
};
const translators = {'zh-CN':tFor('zh-CN'),en:tFor('en')};
const setLocale=async (locale:string)=>patch({locale});
const toggleThemeMode=async ()=>patch({themeMode:state.themeMode==='dark'?'light':'dark'});
export const useUiPrefs = () => { const value=useFixture(); return { ...value, colors:getThemeColors(value.themeMode), t:translators[value.locale], setLocale,toggleThemeMode }; };
export const useIsFocused = () => useFixture().focused;
export const useAppVisibility = () => useFixture().visible;
export const useSafeAreaInsets = () => useFixture().insets;
export const user = {id:'fixture-user',login:'phone-review',name:'Phone 导航验证用户 / Navigation review',email:'fixture@example.invalid',appearance:{}};
const fetches = new Map();
function fixtureFetch(owner:string) {
 if (!fetches.has(owner)) fetches.set(owner, async (url:string, init:any) => {
   records.api.push({owner,url,method:init?.method||'GET'});
   if (init?.method && init.method !== 'GET') throw new Error('Fixture prevents unconfigured writes');
   return new Response(JSON.stringify({servingInstanceId:'engine-'+owner,items:[],templates:[],peers:[],jobs:[],nextCursor:null}),{status:200,headers:{'Content-Type':'application/json'}});
 }); return fetches.get(owner);
}
const sessionActions={updateCurrentUser:async()=>{},refreshUser:async()=>user,signOut:async()=>patch({status:'anonymous'}),
 activateProfile:async(authorityKey:string)=>patch({authorityKey,servingInstanceId:'engine-'+authorityKey}),
 createNewDraft:async()=>{},setActiveConversationId:async()=>{}};
export const useAppSession = () => { const value=useFixture(); return {...value,...sessionActions,user,userAvatarUri:'',adminBaseUrl:'https://fixture.invalid',activeProfileId:value.authorityKey,
 activeConversationId:'session-'+value.authorityKey, newDraftId:'draft-'+value.authorityKey,sessionActivityVersion:0,
 authorizedFetch:fixtureFetch(value.authorityKey), getEngineNowMs:Date.now }; };
export const phoneDrafts = {flushAll:() => {records.flushCalls++;
 if(state.draftMode==='reject')return Promise.reject(new Error('Fixture draft write failed'));
 if(state.draftMode==='hold')return new Promise<void>((resolve,reject)=>records.pendingFlushes.push({resolve,reject}));
 return Promise.resolve();}};
export const fixture: any = { patch, records, getState:()=>state, resetRecords:()=>{records.routes.length=0;records.flushCalls=0;records.api.length=0;},
 setPage:(page:string)=>patch({page}), setDraftMode:(draftMode:string)=>patch({draftMode}), setFocus:(focused:boolean)=>patch({focused}),
 setAuthority:(authorityKey:string)=>patch({authorityKey,servingInstanceId:'engine-'+authorityKey}),
 resolveFlush:()=>records.pendingFlushes.splice(0).forEach((p:any)=>p.resolve()),
 rejectFlush:()=>records.pendingFlushes.splice(0).forEach((p:any)=>p.reject(new Error('Fixture draft write failed'))),
 hardwareBack:()=>document.dispatchEvent(new KeyboardEvent('keyup',{key:'Escape',bubbles:true})),
};
(window as any).phoneNavigationFixture = fixture;
`);
write("router.tsx", `
import React from 'react';
import {patch, records, useFixture} from './fixture-state';
const move=(method:string,target:any)=>{records.routes.push({method,target});const value=typeof target==='string'?target:target.pathname;patch({page:value.replace(/^\\//,'').split('?')[0]||'chat'});};
export const router={navigate:(target:any)=>move('navigate',target),push:(target:any)=>move('push',target),dismissTo:(target:any)=>move('dismissTo',target),replace:(target:any)=>move('replace',target),back:()=>move('back','/chat'),canGoBack:()=>true};
export const usePathname=()=>'/'+useFixture().page;
export const useLocalSearchParams=()=>({});
export const Redirect=({href}:any)=><div>Fixture redirect: {href}</div>;
`);
write("native.tsx", `
import React from 'react';
import * as Native from 'react-native-web';
import { useFixture } from './fixture-state';
export * from 'react-native-web';
const scaled = (props:any,scale:number) => {const s=Native.StyleSheet.flatten(props.style)||{};return [props.style,props.allowFontScaling===false?{}:{fontSize:(s.fontSize||14)*scale,...(s.lineHeight?{lineHeight:s.lineHeight*scale}:{})}];};
export const Text=React.forwardRef((props:any,ref:any)=>{const {fontScale}=useFixture();return <Native.Text {...props} ref={ref} style={scaled(props,fontScale)}/>;});
export const TextInput=React.forwardRef((props:any,ref:any)=>{const {fontScale}=useFixture();return <Native.TextInput {...props} ref={ref} style={scaled(props,fontScale)}/>;});
export const useWindowDimensions=()=>({...Native.useWindowDimensions(),fontScale:useFixture().fontScale});
`);
write("native-shell.tsx", `
import React from 'react';
import {StyleSheet,View} from 'react-native';
import {useSafeAreaInsets} from './fixture-state';
import {Menu, X, User, Power, Monitor, Settings, ChevronDown, Circle} from 'lucide-react';
export {useSafeAreaInsets} from './fixture-state';
export function SafeAreaView({edges=['top','right','bottom','left'],style,children,...rest}:any){const inset=useSafeAreaInsets();const flat=StyleSheet.flatten(style)||{};const padding=Object.fromEntries(edges.map((edge:string)=>{const key='padding'+edge[0].toUpperCase()+edge.slice(1);return [key,(flat[key]??flat[edge==='top'||edge==='bottom'?'paddingVertical':'paddingHorizontal']??flat.padding??0)+inset[edge]];}));return <View {...rest} style={[style,padding]}>{children}</View>;}
export const SafeAreaProvider=({children}:any)=><>{children}</>;
export const LinearGradient=({children,colors,style,...rest}:any)=><View style={[style,{backgroundImage:'linear-gradient(135deg,'+colors.join(',')+')'}]}>{children}</View>;
export const MaterialCommunityIcons=({name,size=20,color}:any)=>{const Icon=({menu:Menu,close:X,account:User,power:Power,'monitor-dashboard':Monitor,'cog-outline':Settings,'chevron-down':ChevronDown} as any)[name]||Circle;return <Icon size={size} color={color} aria-hidden="true"/>;};
export const requestMediaLibraryPermissionsAsync=async()=>({granted:true});
export const launchImageLibraryAsync=async()=>({canceled:true,assets:[]});
export const setStringAsync=async()=>{};
export const ImageManipulator={manipulate:()=>{throw new Error('Hardware crop is outside fixture');}};
export const SaveFormat={JPEG:'jpeg'};
export default function MaskedView({maskElement,style}:any){return <View style={style}>{maskElement}</View>;}
`);
write("animation.ts", `
import {useRef} from 'react';
import {View,Text} from 'react-native';
export default {View,Text};
export const useSharedValue=(value:any)=>useRef({value}).current;
export const useAnimatedStyle=(fn:any)=>fn();
export const useReducedMotion=()=>true;
export const cancelAnimation=()=>{};
export const withTiming=(value:any)=>value;
export const interpolate=(_value:any,_range:any,values:any)=>values[0];
export const Extrapolation={CLAMP:'clamp'};
export const Easing={bezier:()=>()=>0};
`);
write("api.ts", `
import {user,state,records} from './fixture-state';
export const getCurrentProfile=async()=>user;
export const updateProfile=async(_fetch:any,value:any)=>({...user,...value});
export const uploadUserAvatar=async()=>{throw new Error('Fixture upload disabled');};
export const uploadUserBackground=uploadUserAvatar;
export const getRpaAvailability=async()=>({robotFramework:true,rpaFramework:true});
export const listRpaTemplates=async()=>[{id:'fixture.navigation',name:'导航验证模板 / Navigation fixture',goal:'Local fixture only; no automation runs.',variables:[{name:'target',label:'目标设备 / Target device',type:'string',required:true,defaultValue:'Fixture desktop'}]}];
export const runRpaTemplate=async(_fetch:any,id:string,variables:any)=>{records.api.push({kind:'rpa-run',id,variables});return {status:'completed'};};
export const listConversationPage=async()=>({items:[{id:'session-'+state.authorityKey,sessionId:'session-'+state.authorityKey,title:'这是一条用于验证窄屏与大字体的长会话名称 / A very long session title for layout review',workspacePath:'E:/Fixture/workspace',scopeTags:['workspace:main'],sourceGroup:'web',updatedAt:new Date().toISOString(),messageCount:8}],nextCursor:null});
export const deleteConversation=async()=>{};
`);
write("storage.ts", `
import {state,user} from './fixture-state';
const metadata=new Map();
export const readMetadata=async(key:string)=>metadata.get(key)||null;
export const writeMetadata=async(key:string,value:any)=>{metadata.set(key,value);};
export const readAdminConnectionProfiles=async()=>['A','B'].map(id=>({id,instanceId:'engine-'+id,label:'设备 '+id+' · 非常长的工作站名称 / Very long workstation name',adminBaseUrl:'https://fixture-'+id.toLowerCase()+'.invalid',user,lastUsedAt:new Date().toISOString()}));
export const updateAdminConnectionProfiles=async(fn:any)=>fn(await readAdminConnectionProfiles());
export const buildLocalSessionIndexNamespace=(...parts:any[])=>JSON.stringify(parts);
const database={getSessionIndex:async()=>[],setSessionIndex:async()=>{},deleteSessionData:async()=>{}};
export const createLocalDatabase=()=>database;
export const deviceExecutor={forgetProfile:async()=>{}};
export const listSupervisorPeers=async()=>({servingInstanceId:state.servingInstanceId,items:[],nextCursor:null});
export const updateSupervisorPeer=async()=>{};
export const revokeSupervisorPeer=async()=>{};
export const loadPeerTimeline=async()=>({items:[],nextCursor:null});
export const resolveAdminAssetUrl=(base:string,value:string)=>value?new URL(value,base).href:'';
`);
write("entry.tsx", `
import React from 'react';
import {createRoot} from 'react-dom/client';
import {View,Text,TextInput,ScrollView} from 'react-native';
import {SafeAreaView} from 'react-native-safe-area-context';
import {PhoneTopbar} from '@/src/components/layout/PhoneTopbar';
import {ProfileMenuOverlay} from '@/src/components/chat/ProfileMenuOverlay';
import SettingsScreen from '@/src/screens/SettingsScreen';
import SessionsScreen from '@/src/screens/SessionsScreen';
import ConnectScreen from '@/src/screens/ConnectScreen';
import RPAScreen from '@/src/screens/RPAScreen';
import {fixture,patch,records,useFixture,useUiPrefs} from './fixture-state';
function App(){const state=useFixture();const {colors,toggleThemeMode}=useUiPrefs();
 const actions=[{key:'desktop-live',onPress:()=>{records.desktop++},disabled:state.desktopDisabled}, ${baseline ? "{key:'rpa',onPress:()=>{}}," : ""} {key:'theme',onPress:toggleThemeMode}];
 if(state.page==='settings')return <SettingsScreen/>;
 if(state.page==='sessions')return <SessionsScreen/>;
 if(state.page==='connect')return <ConnectScreen/>;
 if(state.page==='rpa')return <RPAScreen/>;
 return <SafeAreaView style={{flex:1,backgroundColor:colors.background}} edges={['top','left','right']}>
  <PhoneTopbar actions={actions} ${baseline ? 'onProfilePress={()=>{records.profile++;patch({profileOpen:true})}}' : ''} onBrandPress={()=>{}}/>
  <View style={{flex:1,padding:16,gap:12}}>
   <Text style={{fontSize:20,fontWeight:'700',color:colors.text}}>Phone {state.page}</Text>
   <Text style={{fontSize:13,color:colors.textMuted}}>真实顶栏与菜单 · 聊天正文为合成夹具</Text>
   <Text style={{fontSize:14,color:colors.text}}>设备 {state.authorityKey} · A very long workstation name</Text>
   <ScrollView style={{flex:1}}><Text style={{fontSize:16,color:colors.text}}>用于验证菜单关闭后回到原页面。Fixture transcript remains local.</Text></ScrollView>
   <TextInput accessibilityLabel="Fixture chat draft" multiline value={state.draft} onChangeText={(draft:string)=>patch({draft})} style={{minHeight:80,padding:12,backgroundColor:colors.surface,color:colors.text,borderRadius:12}}/>
  </View>
  ${baseline ? '<ProfileMenuOverlay visible={state.profileOpen} onClose={()=>patch({profileOpen:false})}/>' : ''}
 </SafeAreaView>;
}
createRoot(document.getElementById('root')!).render(<App/>);
fixture.ready=true;
`);
write("ts-loader.cjs", `const ts=require(${JSON.stringify(require.resolve("typescript"))});module.exports=function(source){return ts.transpileModule(source,{fileName:this.resourcePath,compilerOptions:{jsx:ts.JsxEmit.ReactJSX,target:ts.ScriptTarget.ES2022,module:ts.ModuleKind.ESNext,esModuleInterop:true}}).outputText};`);
write("index.html", '<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Phone navigation UI fixture</title><style>html,body,#root{margin:0;width:100%;height:100%;overflow:hidden}body{font-family:Arial,"Microsoft YaHei",sans-serif}#root{display:flex;flex-direction:column}*{box-sizing:border-box}</style></head><body><div id="root"></div><script src="fixture.js"></script></body></html>');
const alias = {
    ...frozenAliases,
    "react-native$": path.join(output, "native.tsx"),
    "react$": require.resolve("react"), "react-dom$": require.resolve("react-dom"),
    "lucide-react-native$": require.resolve("lucide-react", { paths: [admin] }),
    "@/src/providers/ui-prefs$": path.join(output, "fixture-state.ts"),
    "@/src/providers/app-session$": path.join(output, "fixture-state.ts"),
    "@/src/hooks/use-app-visibility$": path.join(output, "fixture-state.ts"),
    "@/src/lib/phone-drafts$": path.join(output, "fixture-state.ts"),
    "@react-navigation/native$": path.join(output, "fixture-state.ts"),
    "expo-router$": path.join(output, "router.tsx"),
    "react-native-reanimated$": path.join(output, "animation.ts"),
    "@/src/lib/phone-api$": path.join(output, "api.ts"),
};
for (const name of ["react-native-safe-area-context", "expo-linear-gradient", "@expo/vector-icons", "@react-native-masked-view/masked-view", "expo-image-picker", "expo-image-manipulator", "expo-clipboard"]) alias[name + "$"] = path.join(output, "native-shell.tsx");
for (const name of ["@/src/lib/mobile-storage", "@/src/lib/admin-connection-profiles", "@/src/services/LocalDatabaseService", "@/src/lib/device-executor", "@/src/lib/supervisor-peers", "@/src/lib/admin-client"]) alias[name + "$"] = path.join(output, "storage.ts");
alias["@"] = phone;
const sourceSha256 = {};
for (const file of [...snapshots, ...(!baseline ? ["src/components/layout/PhoneNavigationMenu.tsx"] : [])]) {
    const target = baseline ? path.join(output, "baseline", file) : path.join(phone, file);
    if (fs.existsSync(target)) sourceSha256[file] = createHash("sha256").update(fs.readFileSync(target)).digest("hex");
}
write("evidence-boundaries.json", JSON.stringify({ baseline: baseline || null, sourceHead: execFileSync("git", ["rev-parse", "HEAD"], { cwd: repo, encoding: "utf8" }).trim(),
    sourceSha256,
    realComponents: ["PhoneTopbar", "LocaleMenu", "dropdown-menu", "PhoneNavigationMenu (candidate)", "PhoneRpaOverlay", "RpaPanelContent", "ProfileMenuOverlay", "AvatarCropModal", "SettingsScreen", "SessionsScreen", "ConnectScreen", "RPAScreen"],
    boundaries: ["React Native Web rendering and real React; not an Android runtime", "Expo router records calls and changes fixture page; no claim about native stack", "In-memory storage/network/session fixture; no real credentials or writes", "Static animation and icon adapters; Lucide SVG geometry reused", "Synthetic font scaling and safe-area insets; not OS accessibility settings", "Chat body is synthetic; chat hooks/queue/scroll not validated here", "Escape exercises RN Web Modal onRequestClose; not physical Android Back"],
    fixture: "window.phoneNavigationFixture: patch, records, getState, resetRecords, setPage, setDraftMode(immediate/hold/reject), resolveFlush, rejectFlush, setFocus, setAuthority, hardwareBack",
    query: "?page=chat|sessions|connect|settings|rpa&locale=zh-CN|en&theme=light|dark&fontScale=1.8&top=24&bottom=24&left=20&right=20",
}, null, 2));
webpack({ mode: "development", devtool: false, context: phone, entry: path.join(output, "entry.tsx"), output: { path: output, filename: "fixture.js" },
    resolve: { extensions: [".web.tsx", ".web.ts", ".web.js", ".tsx", ".ts", ".js", ".json"], modules: [path.join(phone, "node_modules"), path.join(admin, "node_modules"), "node_modules"], alias },
    module: { rules: [{ test: /\.tsx?$/, exclude: /node_modules/, use: path.join(output, "ts-loader.cjs") }, { test: /\.(png|jpg|ttf)$/, type: "asset/resource" }] },
    plugins: [new webpack.DefinePlugin({ __DEV__: "false" })],
}, (error, stats) => {
    if (error || stats.hasErrors()) { console.error(error || stats.toString({ all: false, errors: true })); process.exitCode = 1; }
    else { const warnings=stats.toJson({all:false,warnings:true}).warnings; write("build-warnings.json", JSON.stringify(warnings,null,2)); console.log(`Phone navigation UI built: ${output} (${warnings.length} warnings)`); }
});
