// Historical 9.13.1 Git object paths below are deliberately frozen; only the current test runtime comes from Product Web.
// Independent roundtrip fixtures executed through frozen production functions.
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import vm from 'node:vm';
import {createRequire} from 'node:module';
import {execFileSync} from 'node:child_process';
const repo=path.resolve(import.meta.dirname,'../../..');
const args=process.argv.slice(2);
const commit=args.includes('--candidate')?args[args.indexOf('--candidate')+1]:'09dfcc3cd231d1717d3e712ad2270750c4c1fe31';
const sourcePath='apps/v8-agent-os-admin/src/app/admin/(dashboard)/extensions/page.tsx';
const source=execFileSync('git',['-C',repo,'show',`${commit}:${sourcePath}`],{encoding:'utf8'});
const require=createRequire(path.join(repo,'apps/v8-agent-os-web/package.json'));
const ts=require('typescript');
const ast=ts.createSourceFile(sourcePath,source,ts.ScriptTarget.Latest,true,ts.ScriptKind.TSX);
const names=['normalizeMcpTransportType','parseMcpArgs','parseMcpKeyValueLines','formatMcpArgsText','formatMcpKeyValueText','mcpFormFromServerConfig','buildMcpFormPayload','validateMcpJsonInput'];
const declarations=ast.statements.filter(n=>ts.isFunctionDeclaration(n)&&names.includes(n.name?.text));
assert.equal(declarations.length,names.length,'adapter requires review if production function ownership changes');
const code=ts.transpileModule(declarations.map(n=>n.getText(ast)).join('\n'),{compilerOptions:{target:ts.ScriptTarget.ES2022}}).outputText;
const context=vm.createContext({structuredClone});vm.runInContext(code,context);
const plain=x=>JSON.parse(JSON.stringify(x));
const invoke=(base,change)=>{const form=context.mcpFormFromServerConfig('synthetic',base);change?.(form);return plain(context.buildMcpFormPayload(form,x=>x)).mcpServers.synthetic;};
const rows=[];
function check(id,fn){try{rows.push({id,status:'PASS',details:fn()});}catch(e){rows.push({id,status:e.name==='AssertionError'?'FAIL':'HARNESS_ERROR',error:String(e)});}}
check('F01-unknown-and-reference-preservation',()=>{
  const base={type:'http',endpointRef:'synthetic-ref',endpointHost:'fixture.invalid',headers:{'X-Keep':'value'},'x-v8-credential-refs':{authorization:{target:'header',targetName:'Authorization',secretRef:'synthetic-header-ref'}},future:{zero:0,flag:false,list:[],nested:{retain:'unchanged'}},disabled:false};
  const result=invoke(base);
  assert.deepEqual(result['x-v8-edit-base'],base);delete result['x-v8-edit-base'];assert.deepEqual(result,base);
  return{unknowns:true,endpointRef:true,headerRefs:true,baseUnmodified:true};
});
check('F02-explicit-clear-and-replace',()=>{
  const base={type:'http',endpointRef:'synthetic-ref',endpointHost:'fixture.invalid',headers:{'X-Keep':'value'},'x-v8-credential-refs':{authorization:{target:'header',targetName:'Authorization',secretRef:'synthetic-header-ref'}},future:{keep:0}};
  const result=invoke(base,form=>{form.clearedCredentials=['endpointRef','authorization'];form.url='https://replacement.fixture.invalid/mcp';form.headersText='X-Keep=value';});
  assert.equal(result.endpointRef,undefined);assert.equal(result.endpointHost,undefined);assert.deepEqual(result['x-v8-credential-refs'],{});assert.equal(result.url,'https://replacement.fixture.invalid/mcp');assert.deepEqual(result.future,{keep:0});
  assert.equal(base.endpointRef,'synthetic-ref');return{explicitClear:true,newEndpoint:true,unknownPreserved:true,originalBaseRetained:true};
});
check('F03-stdio-argument-roundtrip',()=>{
  const base={type:'stdio',command:'synthetic-command',args:['--label','  spaced value  ',''],env:{KEEP:'original'},future:{keep:false}};
  const result=invoke(base);
  assert.deepEqual(result.args,base.args,'opening and saving an unchanged stdio server must preserve the exact argv including empty arguments and boundary spaces');
  return{argumentArrayUnchanged:true};
});
check('F04-argument-boundary-matrix',()=>{
  const matrix=[[],[''],['',''],['line one\nline two'],['CR\r\nLF'],['quote"','slash\\end'],['  leading','trailing  ']];
  for(const argv of matrix){const base={type:'stdio',command:'synthetic-command',args:argv,env:{}};assert.deepEqual(invoke(base).args,argv);}
  return{cases:matrix.length,emptyArray:true,emptyArguments:true,embeddedNewlines:true,quotesAndBackslashes:true};
});
check('F05-invalid-json-arguments-rejected',()=>{
  const base={type:'stdio',command:'synthetic-command',args:[],env:{}};
  for(const value of ['[1]','[null]','[{}]','["unfinished"'])assert.throws(()=>invoke(base,form=>{form.argsText=value;}));
  return{invalidInputs:4,noPayloadProduced:true};
});
const out=path.join(repo,`scripts/experience/9131/reports/extensions-form-${commit.slice(0,8)}.json`);
fs.writeFileSync(out,JSON.stringify({commit,sourcePath,level:'BOUNDARY_EXECUTED',qualification:'Frozen real form functions with synthetic values; not native process execution or full BFF credential mutation.',rows},null,2)+'\n');
console.log(JSON.stringify(rows));process.exitCode=rows.some(r=>r.status!=='PASS')?1:0;
