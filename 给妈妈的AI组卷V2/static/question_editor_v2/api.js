export class ContentApiError extends Error {
  constructor(status, payload) {super(payload?.error?.message || `请求失败（${status}）`);this.status=status;this.code=payload?.error?.code;this.details=payload?.error?.details;this.payload=payload;}
}
export function createContentApi({baseUrl='',fetchImpl=globalThis.fetch, headers={}}={}) {
  const send=async(path,{method='GET',body,signal}={})=>{
    const response=await fetchImpl(`${baseUrl}${path}`,{method,signal,credentials:'same-origin',headers:{...headers,...(body?{'Content-Type':'application/json'}:{})},body:body?JSON.stringify(body):undefined});
    let payload;try{payload=await response.json();}catch{throw new ContentApiError(response.status,{error:{message:'服务返回了无法读取的内容。请保留草稿并重试。'}});}
    if(!response.ok)throw new ContentApiError(response.status,payload);return payload;
  };
  return {
    getQuestion:(id,{signal}={})=>send(`/api/v2/content/questions/${encodeURIComponent(id)}`,{signal}),
    saveQuestion:(id,body,{signal}={})=>send(`/api/v2/content/questions/${encodeURIComponent(id)}`,{method:'PUT',body,signal}),
    compileFormula:(body,{signal}={})=>send('/api/v2/content/formulas/compile',{method:'POST',body,signal}),
    getPaper:(id,{signal}={})=>send(`/api/v2/content/papers/${encodeURIComponent(id)}`,{signal}),
    exportPaper:(id,body,{signal}={})=>send(`/api/v2/content/papers/${encodeURIComponent(id)}/export`,{method:'POST',body,signal})
  };
}
