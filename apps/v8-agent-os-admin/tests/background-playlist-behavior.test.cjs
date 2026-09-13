const test=require('node:test');const assert=require('node:assert/strict');const fs=require('node:fs');const os=require('node:os');const path=require('node:path');const vm=require('node:vm');const ts=require('typescript');
const compile=(file)=>ts.transpileModule(fs.readFileSync(file,'utf8'),{compilerOptions:{module:ts.ModuleKind.CommonJS,target:ts.ScriptTarget.ES2022,esModuleInterop:true}}).outputText;
function fixture(t){
 const root=fs.mkdtempSync(path.join(os.tmpdir(),'v8-background-contract-'));t.after(()=>fs.rmSync(root,{recursive:true,force:true}));
 const schema={};vm.runInNewContext(compile(path.resolve(__dirname,'../../../packages/product-ui/src/background-playlist.ts')),{exports:schema,require});
 const user={id:'owner',login:'owner',password:'synthetic-hash',role:'ADMIN',createdAt:'2026-09-13T00:00:00Z',appearance:{lightBackgroundMedia:'/user-assets/background/phone.webp',lightBackgroundEnabled:true,unknownField:'keep'}};
 let payload={users:[user]};const exports={};fs.writeFileSync(path.join(root,'users.json'),'fixture');
 const importer=(name)=>{
  if(name==='@/lib/storage')return{AdminStorageUnavailableError:Error,getBaseDir:()=>root,readJsonStrict:()=>JSON.parse(JSON.stringify(payload)),writeJsonStrict:(_name,value)=>{payload=JSON.parse(JSON.stringify(value))}};
  if(name==='@v8/product-ui/background-playlist')return schema;
  if(name==='@/i18n/internal-readable')return{INTERNAL_READABLE:{}};
  return require(name);
 };
 vm.runInNewContext(compile(path.resolve(__dirname,'../src/lib/users.ts')),{exports,require:importer,process,SharedArrayBuffer,Int32Array,Atomics});
 const directory=path.join(root,'assets/user-media/background');fs.mkdirSync(directory,{recursive:true});
 for(const file of ['phone.webp','one.webp','two.mp4']){fs.writeFileSync(path.join(directory,file),'fixture');fs.writeFileSync(path.join(directory,'.receipt-'+file+'.json'),JSON.stringify({userId:'owner',kind:file.endsWith('mp4')?'video':'image'}));}
 const loadModule=(relative)=>{const value={};vm.runInNewContext(compile(path.resolve(__dirname,'../src',relative)),{exports:value,require(name){if(name==='@/lib/users')return exports;if(name==='@/lib/user-media')return loadModule('lib/user-media.ts');if(name==='@/lib/background-media')return loadModule('lib/background-media.ts');if(name==='@/lib/server/client-proxy')return{requireClientContext:async()=>({user:exports.findUserById('owner')})};if(name==='@/lib/server/native-image-processing')return{getNativeImageProcessingAvailability:()=>({available:true})};return importer(name)},process,Buffer,File,console});return value;};
 return{users:exports,schema,root,loadModule};
}
test('playlist revision saves preserve Phone and unknown appearance fields; stale writes lose',t=>{
 const f=fixture(t);const playlist={revision:0,enabled:true,imageDurationMs:1000,items:[{id:'one',kind:'image',media:'/user-assets/background/one.webp'},{id:'two',kind:'video',media:'/user-assets/background/two.mp4'}]};
 const saved=f.users.updateUserRecord('owner',{appearance:{webBackground:playlist}});
 assert.equal(saved.appearance.webBackground.revision,1);assert.equal(saved.appearance.lightBackgroundMedia,'/user-assets/background/phone.webp');
 assert.throws(()=>f.users.updateUserRecord('owner',{appearance:{webBackground:playlist}}),/BACKGROUND_REVISION_CONFLICT/);
 const phone=f.users.updateUserRecord('owner',{name:'phone rename',appearance:{lightBackgroundEnabled:false}});
 assert.equal(phone.appearance.webBackground.items.length,2);assert.equal(phone.appearance.unknownField,'keep');
});
test('managed media references cannot be fabricated and duplicate resource references survive reorder',t=>{
 const f=fixture(t);assert.throws(()=>f.users.updateUserRecord('owner',{appearance:{webBackground:{items:[{id:'bad',kind:'image',media:'/user-assets/background/missing.webp'}]}}}),/BACKGROUND_MEDIA_UNAVAILABLE/);
 const value=f.schema.normalizeBackgroundPlaylist({items:[{id:'a',media:'/user-assets/background/one.webp',kind:'image'},{id:'b',media:'/user-assets/background/one.webp',kind:'image'}]});
 assert.equal(value.items.length,2);assert.equal(f.schema.referencedBackgroundMedia({webBackground:value}).size,1);
 assert.throws(()=>f.schema.normalizeBackgroundPlaylist({imageDurationMs:0}),/间隔/);
 assert.throws(()=>f.schema.normalizeBackgroundPlaylist({items:[{id:'a',media:'https://untrusted.invalid/video.mp4',kind:'video'}]}));
});
test('two actual image uploads return receipts without activating or deleting the first asset',async t=>{
 const f=fixture(t);const upload=f.loadModule('app/api/client/user-background-upload/route.ts');const {NextRequest}=require('next/server');
 const bytes=await require('sharp')({create:{width:8,height:8,channels:3,background:'#d22846'}}).png().toBuffer();
 const paths=[];
 for(let index=0;index<2;index++){
  const req=new NextRequest('http://fixture.invalid/upload',{method:'POST',headers:{'content-type':'image/png','x-v8-upload-mode':'raw','x-v8-background-intent':'playlist'},body:bytes,duplex:'half'});
  const response=await upload.POST(req);assert.equal(response.status,200);const result=await response.json();assert.ok(result.receipt);paths.push(result.path);
 }
 assert.equal(f.users.findUserById('owner').appearance.lightBackgroundMedia,'/user-assets/background/phone.webp');
 for(const media of paths)assert.ok(fs.existsSync(path.join(f.root,'assets/user-media/background',path.basename(media))));
 f.users.updateUserRecord('owner',{appearance:{webBackground:{revision:0,enabled:true,items:paths.map((media,i)=>({id:'item-'+i,kind:'image',media}))}}});
 f.loadModule('lib/background-media.ts').removeUnreferencedBackground(paths[0]);
 assert.ok(fs.existsSync(path.join(f.root,'assets/user-media/background',path.basename(paths[0]))),'referenced media cannot be removed by legacy replacement');
});
