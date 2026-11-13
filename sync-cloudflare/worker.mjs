const E=new TextEncoder(), D=new TextDecoder();
const out=(status,payload)=>new Response(JSON.stringify(payload),{status,headers:{'content-type':'application/json; charset=utf-8','cache-control':'no-store','x-content-type-options':'nosniff'}});
const bytesTo64=a=>btoa(String.fromCharCode(...a));
const b64ToBytes=s=>Uint8Array.from(atob(s),c=>c.charCodeAt(0));
async function key(env){return crypto.subtle.importKey('raw',b64ToBytes(env.ENCRYPTION_KEY),'AES-GCM',false,['encrypt','decrypt']);}
async function samePassword(input,env){
 if(typeof input!=='string'||!input||input.length>512||!env.READ_PASSWORD)return false;
 const message=E.encode('proxy-deep-read-auth-v2');
 const expected=await crypto.subtle.importKey('raw',E.encode(env.READ_PASSWORD),{name:'HMAC',hash:'SHA-256'},false,['sign']);
 const candidate=await crypto.subtle.importKey('raw',E.encode(input),{name:'HMAC',hash:'SHA-256'},false,['verify']);
 const signature=await crypto.subtle.sign('HMAC',expected,message);
 return crypto.subtle.verify('HMAC',candidate,signature,message);
}
export default {async fetch(req,env){
 const path=new URL(req.url).pathname;
 if(req.method!=='POST'||!['/accounts','/accounts/retrieve'].includes(path))return out(404,{error:'not_found'});
 if(!(req.headers.get('content-type')||'').toLowerCase().startsWith('application/json'))return out(415,{error:'json_required'});
 const ip=req.headers.get('CF-Connecting-IP')||'unknown';
 const limiter=path==='/accounts/retrieve'?env.READ_LIMIT:env.WRITE_LIMIT;
 if(!limiter||!(await limiter.limit({key:ip})).success)return out(429,{error:'rate_limited'});
 const size=Number(req.headers.get('content-length'));
 if(!Number.isFinite(size)||size<1||size>16384)return out(413,{error:'invalid_size'});
 let payload;try{payload=await req.json()}catch{return out(400,{error:'invalid_json'})}
 if(path==='/accounts'){
  if(!payload||typeof payload!=='object'||Array.isArray(payload)||
     Object.keys(payload).length!==1||!Object.hasOwn(payload,'account'))return out(400,{error:'invalid_payload'});
  const c=payload.account;
  if(!c||typeof c!=='object'||Array.isArray(c)||
     Object.keys(c).some(k=>k!=='deepai_key'&&k!=='machine')||
     typeof c.deepai_key!=='string'||! /^[A-Za-z0-9_-]{16,256}$/.test(c.deepai_key)||
     (c.machine!==undefined&&(typeof c.machine!=='string'||! /^[a-fA-F0-9]{64}$/.test(c.machine))))
    return out(400,{error:'invalid_account'});
  const account={deepai_key:c.deepai_key,...(c.machine?{machine:c.machine}:{})};
  const fingerprint=bytesTo64(new Uint8Array(await crypto.subtle.digest('SHA-256',E.encode(c.deepai_key))));
  const count=await env.DB.prepare('SELECT count(*) AS n FROM protected_accounts').first();
  if(count.n>=1000){const exists=await env.DB.prepare('SELECT 1 AS yes FROM protected_accounts WHERE id = ?').bind(fingerprint).first();if(!exists)return out(507,{error:'capacity'});}
  const iv=crypto.getRandomValues(new Uint8Array(12));
  const ciphertext=await crypto.subtle.encrypt({name:'AES-GCM',iv,additionalData:E.encode('proxy-deep-account-v2')},await key(env),E.encode(JSON.stringify(account)));
  await env.DB.prepare('INSERT INTO protected_accounts(id,nonce,ciphertext) VALUES(?,?,?) ON CONFLICT(id) DO UPDATE SET nonce=excluded.nonce,ciphertext=excluded.ciphertext').bind(fingerprint,bytesTo64(iv),bytesTo64(new Uint8Array(ciphertext))).run();
  return out(200,{ok:true});
 }
 if(!await samePassword(payload?.password,env))return out(401,{error:'unauthorized'});
 const result=await env.DB.prepare('SELECT nonce,ciphertext FROM protected_accounts ORDER BY rowid LIMIT 1000').all();
 const accounts=[];
 try{
  const cipherKey=await key(env);
  for(const row of result.results){const data=await crypto.subtle.decrypt({name:'AES-GCM',iv:b64ToBytes(row.nonce),additionalData:E.encode('proxy-deep-account-v2')},cipherKey,b64ToBytes(row.ciphertext));accounts.push(JSON.parse(D.decode(data)));}
 }catch{return out(500,{error:'stored_data_unavailable'})}
 return out(200,{accounts});
}};
