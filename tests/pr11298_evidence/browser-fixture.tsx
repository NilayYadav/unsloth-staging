// SPDX-License-Identifier: AGPL-3.0-only
// Copyright 2026-present the Unsloth AI Inc. team. All rights reserved.
import { Thread } from '@/components/assistant-ui/thread';
import { TooltipProvider } from '@/components/ui/tooltip';
import { AssistantRuntimeProvider, useAui, useLocalRuntime } from '@assistant-ui/react';
import { RouterProvider, createMemoryHistory, createRootRoute, createRouter } from '@tanstack/react-router';
import { useEffect, useState } from 'react';
import { createRoot } from 'react-dom/client';
import { registerLiveThreadView } from '@/features/chat/utils/live-thread-head';
import { buildConversationMarkdownForThread, exportConversationMarkdown, exportConversationCsv, saveChatItemAsProjectSource } from '@/features/chat/prompt-storage/prompt-storage-dialog';
import './src/index.css';

const rows = [
  { id: 'u1', parentId: null, role: 'user', content: [{type:'text',text:'Name one fruit.'}] },
  { id: 'a1', parentId: 'u1', role: 'assistant', content: [{type:'text',text:'Apples.'}] },
  { id: 'a2', parentId: 'u1', role: 'assistant', content: [{type:'text',text:'Pears.'}] },
].map((m,i)=>({...m, threadId:'evidence-thread', createdAt: 1720000000000+i, attachments:[], metadata:{}, status: m.role==='assistant'?{type:'complete',reason:'stop'}:undefined}));
const facts:any = { uploads: [], requests: [] };
(window as any).__facts = facts;
const originalFetch = window.fetch.bind(window);
window.fetch = async (input, init) => {
  const url = typeof input==='string'?input:(input as Request).url;
  if (!url.includes('/api/')) return originalFetch(input,init);
  facts.requests.push({url, method:init?.method??'GET'});
  let body:any;
  if (/\/api\/chat\/threads\/[^/]+\/messages$/.test(url)) body={messages:rows};
  else if (/\/api\/chat\/threads\/[^/]+\/forks$/.test(url)) body={counts:{}};
  else if (/\/api\/chat\/projects/.test(url)) body={projects:[]};
  else if (/\/api\/rag\/knowledge-bases/.test(url)) body={knowledge_bases:[]};
  else if (/\/api\/rag\/projects\/evidence-project\/documents/.test(url) && init?.method==='POST') {
    const file = (init.body as FormData).get('file') as File;
    facts.uploads.push({filename:file.name,body:await file.text()});
    body={jobId:'evidence-job', filename:file.name};
  } else if (/\/api\/rag\/jobs\/evidence-job/.test(url)) body={status:'completed'};
  else throw new Error('Unexpected fixture API: '+url);
  return new Response(JSON.stringify(body),{status:200,headers:{'content-type':'application/json'}});
};
function Actions() {
  const aui=useAui(); const [result,setResult]=useState('');
  useEffect(()=>{
    aui.thread().import({headId:'a2', messages:rows.map(m=>({parentId:m.parentId,message:{...m,createdAt:new Date(m.createdAt)}}))} as any);
    const remove=registerLiveThreadView({threadListItem:()=>({getState:()=>({remoteId:'evidence-thread'})}),thread:()=>aui.thread()});
    (window as any).__branch=()=>aui.thread().getState().messages.map(m=>m.id);
    (window as any).__ready=true;
    return remove;
  },[aui]);
  return <section style={{padding:20,borderLeft:'1px solid #bbb',width:440,flexShrink:0}}>
    <h2 style={{fontWeight:700,fontSize:20}}>Markdown action evidence</h2>
    <p style={{margin:'12px 0'}}>Browser fixture: real Thread and exporters; seeded history and upload API.</p>
    <div style={{display:'flex',gap:8,flexWrap:'wrap'}}>
      <button className="border rounded p-2" onClick={async()=>{const text=await buildConversationMarkdownForThread('evidence-thread');await navigator.clipboard.writeText(text!);facts.copied=text;setResult(text!);}}>Copy Markdown</button>
      <button className="border rounded p-2" onClick={()=>exportConversationMarkdown('evidence-thread')}>Download Markdown</button>
      <button className="border rounded p-2" onClick={()=>exportConversationCsv('evidence-thread')}>Download CSV</button>
      <button className="border rounded p-2" onClick={async()=>{await saveChatItemAsProjectSource({id:'evidence-thread',title:'Fruit reply',type:'single'},'evidence-project');setResult(facts.uploads.at(-1).body);}}>Save project source</button>
    </div>
    <h3 style={{marginTop:24,fontWeight:700}}>Actual action output</h3>
    <pre data-testid="output" style={{whiteSpace:'pre-wrap',padding:16,marginTop:12,background:'#f1f5f9',color:'#0f172a',borderRadius:8}}>{result}</pre>
  </section>;
}
function Harness(){const runtime=useLocalRuntime({run:()=>{throw new Error('No model calls in fixture')}});return <TooltipProvider><AssistantRuntimeProvider runtime={runtime}><div style={{height:'100vh',display:'flex'}}><div style={{display:'flex',flexDirection:'column',flex:1,minWidth:0}}><Thread hideWelcome /></div><Actions/></div></AssistantRuntimeProvider></TooltipProvider>}
const rootRoute=createRootRoute({component:Harness});
const router=createRouter({routeTree:rootRoute,history:createMemoryHistory({initialEntries:['/']})});
createRoot(document.getElementById('root')!).render(<RouterProvider router={router as never}/>);
