import { useCallback, useEffect, useState } from 'react';
import type { FormEvent } from 'react';
import { createRoot } from 'react-dom/client';
import './style.css';

const kinds = ['denial_notice', 'submission_receipt', 'authorization_record', 'cover_sheet'] as const;
type Kind = typeof kinds[number];
type Role = 'operator' | 'reviewer';
type Document = {kind:Kind; text:string};
type Packet = {claim_ref:string; documents:Document[]};
type State='queued'|'needs_review'|'returned'|'approved';
type Summary = {id:string; claim_ref:string; state:State; version:number};
type Draft={reason:string; provider:string; policy:string; eligible_for_approval:boolean;
  evidence:null|{document:string;quote:string}; required:Kind[]; missing:Kind[]};
type Audit={version:number;actor:string;action:string;note:string|null;recorded_at:string};
type Case=Summary&{documents:Document[]; draft:Draft|null; audit:Audit[]};
type Session={role:Role; token:string};

function label(s:string) {
  return s.replaceAll('_',' ').replace(/^./,m=>m.toUpperCase());
}
async function api<T>(url:string,token?:string,body?:object,key?:string) {
  const headers:Record<string,string>={};
  if(token) headers.Authorization='Bearer '+token;
  if(body) headers['Content-Type']='application/json';
  if(key) headers['Idempotency-Key']=key;
  const r=await fetch(url, {method:body?'POST':'GET',headers,body:body?JSON.stringify(body):undefined});
  const payload=await r.json();
  if(!r.ok) throw new Error(typeof payload.detail==='string'?payload.detail:'Request failed: '+r.status);
  return payload as T;
}
function Badge({state}:{state:State}) { return <span className={'badge '+state}>{label(state)}</span>; }
function App(){
  const [session,setSession]=useState<Session>();
  const [items,setItems]=useState<Summary[]>([]);
  const [selected,setSelected]=useState<string>();
  const [detail,setDetail]=useState<Case>();
  const [feedback,setFeedback]=useState('');
  const [busy,setBusy]=useState(false);
  const [packet,setPacket]=useState<Packet>({claim_ref:'',documents:[]});
  const [note,setNote]=useState('');

  const sync=useCallback(async(token:string,id?:string)=>{
    const [list, record]=await Promise.all([
      api<{cases:Summary[]}>('/api/cases',token),
      id ? api<Case>('/api/cases/'+id,token):Promise.resolve(undefined),
    ]);
    setItems(list.cases); setDetail(record);
  },[]);
  useEffect(()=>{
    if(!session || busy) return;
    let active=true;
    const refresh=async()=>{
      try {
        const token=session.token;
        const q=await api<{cases:Summary[]}>('/api/cases',token);
        const d=selected?await api<Case>('/api/cases/'+selected,token):undefined;
        if(active){setItems(q.cases);setDetail(d);}
      } catch(err){if(active)setFeedback(String(err));}
    };
    void refresh(); const timer=setInterval(()=>void refresh(),2200);
    return()=>{active=false;clearInterval(timer);};
  },[session,selected,busy]);
  async function run(fn:()=>Promise<void>){
    setFeedback('');setBusy(true);
    try{await fn();}catch(err){setFeedback(err instanceof Error?err.message:'Request failed.');}finally{setBusy(false);}
  }
  function signIn(e:FormEvent<HTMLFormElement>){
    e.preventDefault();
    const fields=new FormData(e.currentTarget);
    const role=String(fields.get('role')) as Role;
    void run(async()=>{
      const data=await api<{access_token:string}>('/api/demo/token',undefined,{role,access_key:fields.get('key')});
      setItems([]);setSelected(undefined);setDetail(undefined);setNote('');
      setSession({role,token:data.access_token});
    });
  }
  function openCase(x:Summary){
    setSelected(x.id);setDetail(undefined);setNote('');setFeedback('');
  }
  function upload(e:FormEvent<HTMLFormElement>){
    e.preventDefault();
    if(!session) return;
    void run(async()=>{
      const keyFields=JSON.stringify(packet);
      const hash=await crypto.subtle.digest('SHA-256',new TextEncoder().encode(keyFields));
      const key=Array.from(new Uint8Array(hash),n=>n.toString(16).padStart(2,'0')).join('');
      const out=await api<{id:string}>('/api/cases',session.token,packet,key);
      setSelected(out.id); await sync(session.token,out.id);
    });
  }
  function changeDoc(kind:Kind,text:string){
    const docs=packet.documents.filter(d=>d.kind!==kind);
    if(text.trim()) docs.push({kind,text});
    setPacket({...packet,documents:docs});
  }
  function decision(e:FormEvent<HTMLFormElement>){
    e.preventDefault(); if(!session||!detail) return;
    const submitter=(e.nativeEvent as SubmitEvent).submitter as HTMLButtonElement|null;
    const outcome=submitter?.value;
    if(outcome!=='approve'&&outcome!=='return') return;
    void run(async()=>{
      await api('/api/cases/'+detail.id+'/decision',session.token,{outcome,version:detail.version,note});
      setNote(''); await sync(session.token,detail.id);
    });
  }
  function revise(e:FormEvent<HTMLFormElement>){
    e.preventDefault(); if(!session||!detail) return;
    const form=new FormData(e.currentTarget);
    const documents=kinds.flatMap(kind=>{
      const value=String(form.get(kind)||'').trim();
      return value?[{kind,text:value}]:[];
    });
    void run(async()=>{
      await api('/api/cases/'+detail.id+'/revisions',session.token,{version:detail.version,packet:{claim_ref:detail.claim_ref,documents}});
      await sync(session.token,detail.id);
    });
  }

  return <>
    <header className="mast"><div className="wrap bar"><div className="mark">CR</div><div><p>OPERATIONS WORKBENCH</p><h1>Claims Workflow Review</h1></div><span className="sample">Synthetic data only</span></div></header>
    <main className="wrap">
      {feedback && <p className="error" role="alert">{feedback}</p>}
      {!session ? <section className="panel login"><h2>Open the local workbench</h2><p>Choose an operator to create packets or a reviewer to check the evidence and record a decision.</p>
        <form onSubmit={signIn} className="stack"><label>Demo role<select name="role"><option value="operator">Operator</option><option value="reviewer">Reviewer</option></select></label>
          <label>Local access key<input name="key" type="password" required autoComplete="off"/></label><button disabled={busy}>Sign in</button></form></section>:
      <><div className="toolbar"><div><span className="eyebrow">SIGNED IN AS</span> <strong>{label(session.role)}</strong></div>
        <button className="quiet" disabled={busy} onClick={()=>{setSession(undefined);setFeedback('');setPacket({claim_ref:'',documents:[]});}}>Switch role</button></div>
        <div className="layout"><aside>
          <section className="panel"><h2>Review queue</h2>
          {!items.length&&<p className="muted">No packets yet.</p>}
          {items.map(x=><button key={x.id} disabled={busy} aria-pressed={selected===x.id} className="case" onClick={()=>openCase(x)}><strong>{x.claim_ref}</strong><Badge state={x.state}/><small>Revision {x.version}</small></button>)}
          </section>
          {session.role==='operator'&&<section className="panel"><h2>New packet</h2><div className="examples">
            {(['ready','blocked'] as const).map(k=><button key={k} className="quiet" disabled={busy} onClick={()=>void run(async()=>setPacket((await api<Record<typeof k,Packet>>('/api/demo/fixtures'))[k]))}>{label(k)} example</button>)}
            </div><form onSubmit={upload} className="stack">
            <label>Claim reference<input required value={packet.claim_ref} placeholder="DEMO-NEW" pattern="DEMO-[A-Z0-9\-]{1,32}" onChange={e=>setPacket({...packet,claim_ref:e.target.value})}/></label>
            {kinds.map(kind=><label key={kind}>{label(kind)}<textarea required={kind==='denial_notice'} maxLength={1800} rows={2} value={packet.documents.find(d=>d.kind===kind)?.text||''} onChange={e=>changeDoc(kind,e.target.value)}/></label>)}
            <button disabled={busy}>Queue packet</button>
          </form></section>}
        </aside><div>
          {!detail?<section className="panel"><h2>{selected?'Loading packet…':'Select a packet'}</h2><p className="muted">The review workspace shows the exact source quote, missing items, and who made each decision.</p></section>:
          <><section className="panel" aria-label="Packet summary"><div className="summary"><div><p className="eyebrow">REVISION {detail.version}</p><h2>{detail.claim_ref}</h2></div><Badge state={detail.state}/></div>
          {detail.draft?<><p className="eyebrow">VERIFIED DENIAL EVIDENCE</p>
            {detail.draft.evidence?<blockquote>{detail.draft.evidence.quote}<cite>{label(detail.draft.evidence.document)}</cite></blockquote>:<p>No supported denial reason was verified. Return this packet for clarification.</p>}
            <div className="chiprow">{detail.draft.required.map(k=><span key={k} className={detail.draft?.missing.includes(k)?'missing':'present'}>{label(k)} · {detail.draft?.missing.includes(k)?'missing':'present'}</span>)}</div>
            <p className="muted">Source: {label(detail.draft.provider)} · {detail.draft.policy}</p></>:<p className="muted">{detail.state==='queued'?'Worker is preparing the packet.':'No verified draft is available.'}</p>}
          </section>
          <section className="panel"><h2>Packet documents</h2>{detail.documents.map(d=><details key={d.kind}><summary>{label(d.kind)}</summary><p>{d.text}</p></details>)}</section>
          {session.role==='reviewer'&&detail.state==='needs_review'&&<section className="panel"><h2>Record a decision</h2>
            <form onSubmit={decision} className="stack"><label>Review note<textarea rows={3} required minLength={3} maxLength={500} value={note} onChange={e=>setNote(e.target.value)}/></label>
              <div className="chiprow"><button disabled={busy||!detail.draft?.eligible_for_approval} type="submit" value="approve">Approve packet</button><button disabled={busy} className="quiet" type="submit" value="return">Return for rework</button></div></form>
          </section>}
          {session.role==='operator'&&detail.state==='returned'&&<section className="panel"><h2>Revise and resubmit</h2><form className="stack" key={detail.id+detail.version} onSubmit={revise}>
            {kinds.map(kind=><label key={kind}>{label(kind)}<textarea name={kind} rows={2} maxLength={1800} required={kind==='denial_notice'} defaultValue={detail.documents.find(d=>d.kind===kind)?.text}/></label>)}
            <button disabled={busy}>Submit new revision</button></form></section>}
          <section className="panel"><h2>Audit history</h2><ol className="audit">{detail.audit.map((event,i)=><li key={i}><strong>{label(event.action)}</strong> · {event.actor} · v{event.version}<small>{event.recorded_at} UTC</small>{event.note&&<p>{event.note}</p>}</li>)}</ol></section></>}
        </div></div>
      </>}
    </main></>;
}

createRoot(document.getElementById('root')!).render(<App/>);
