// Recebe somente dados previamente cifrados pelo cliente.
const MAX_BODY = 256 * 1024;
const encoder = new TextEncoder();
const headers = {'content-type':'application/json; charset=utf-8', 'cache-control':'no-store', 'x-content-type-options':'nosniff'};
const reply = (status, body, extra={}) => new Response(JSON.stringify(body), {status, headers:{...headers,...extra}});
async function authorized(request, secret) {
  const value = request.headers.get('authorization') || '';
  if (!value.startsWith('Bearer ') || !secret || secret.length < 32) return false;
  const supplied = encoder.encode(value.slice(7));
  const expected = encoder.encode(secret);
  const key = await crypto.subtle.importKey('raw', expected, {name:'HMAC',hash:'SHA-256'}, false, ['sign','verify']);
  const digest = await crypto.subtle.sign('HMAC',key,encoder.encode('deepai-sync-auth-v1'));
  const other = await crypto.subtle.importKey('raw',supplied,{name:'HMAC',hash:'SHA-256'},false,['verify']);
  return crypto.subtle.verify('HMAC',other,digest,encoder.encode('deepai-sync-auth-v1'));
}
export default {
  async fetch(request, env) {
    if (new URL(request.url).pathname !== '/vault') return reply(404,{error:'not_found'});
    if (!await authorized(request, env.SYNC_TOKEN)) return reply(401,{error:'unauthorized'});
    if (request.method === 'GET') {
      const row = await env.DB.prepare('SELECT revision, envelope FROM vault WHERE id = 1').first();
      if (!row) return reply(404,{error:'not_initialized'});
      return reply(200,{revision:row.revision,envelope:JSON.parse(row.envelope)});
    }
    if (request.method !== 'PUT') return reply(405,{error:'method_not_allowed'},{allow:'GET, PUT'});
    const length = Number(request.headers.get('content-length'));
    if (!Number.isFinite(length) || length <= 0 || length > MAX_BODY) return reply(413,{error:'invalid_size'});
    const text = await request.text();
    if (encoder.encode(text).length > MAX_BODY) return reply(413,{error:'invalid_size'});
    let payload;
    try { payload=JSON.parse(text); } catch { return reply(400,{error:'invalid_json'}); }
    if (!Number.isSafeInteger(payload.revision) || payload.revision<0 ||
        typeof payload.envelope!=='object' || !payload.envelope ||
        payload.envelope.version!==1 || payload.envelope.algorithm!=='AES-256-GCM' ||
        typeof payload.envelope.ciphertext!=='string' || typeof payload.envelope.nonce!=='string' ||
        typeof payload.envelope.salt!=='string') return reply(400,{error:'invalid_payload'});
    const serialized=JSON.stringify(payload.envelope);
    if (payload.revision===0) {
      const result=await env.DB.prepare('INSERT OR IGNORE INTO vault (id, revision, envelope) VALUES (1, 1, ?)').bind(serialized).run();
      if (!result.meta.changes) return reply(409,{error:'conflict'});
      return reply(200,{revision:1});
    }
    const next=payload.revision+1;
    const result=await env.DB.prepare('UPDATE vault SET revision = ?, envelope = ? WHERE id = 1 AND revision = ?').bind(next,serialized,payload.revision).run();
    if (!result.meta.changes) return reply(409,{error:'conflict'});
    return reply(200,{revision:next});
  }
};
