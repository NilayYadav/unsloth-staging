"""Search 201 real persisted chats, then open the oldest matching title."""
import asyncio
import json
from pathlib import Path
import httpx
from playwright.async_api import async_playwright

TITLE = 'Zanzibar ledger'
TOTAL = 201
EXPECT = 'The oldest of 201 chats has no search result BEFORE and one title result AFTER; message fetches remain bounded to 200.'

async def drive(session, out_dir, label, **kwargs):
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    headers = {'Authorization': f'Bearer {session.access_token}'}
    async with httpx.AsyncClient(base_url=session.base_url, headers=headers, timeout=60) as client:
        async def seed(i):
            tid = f'evidence-thread-{i:03d}'
            title = TITLE if i == TOTAL-1 else f'Chat {i:03d}'
            ts = 1789758000000 - i * 60000
            thread = {'id':tid,'title':title,'modelType':'base','modelId':'','archived':False,'createdAt':ts,'updatedAt':ts}
            r = await client.post('/api/chat/threads', json=thread)
            r.raise_for_status()
            r = await client.put(f'/api/chat/threads/{tid}/messages', json={'messages':[{'id':f'message-{i}','threadId':tid,'role':'user','content':[{'type':'text','text':f'Ledger entry {i} quokka'}],'createdAt':ts}]})
            r.raise_for_status()
        for start in range(0, TOTAL, 10):
            await asyncio.gather(*(seed(i) for i in range(start,min(start+10,TOTAL))))
        r = await client.get('/api/chat/threads')
        r.raise_for_status()
        threads = r.json()['threads']
        assert len(threads) == TOTAL, len(threads)
        assert threads[-1]['title'] == TITLE

    facts = {'seeded_threads':len(threads),'oldest_title':TITLE,'oldest_thread':threads[-1]['id'], 'batch_sizes':[], 'batch_contains_oldest':[]}
    async with async_playwright() as p:
        browser = await p.chromium.launch()
        context = await browser.new_context(viewport={'width':1280,'height':900},color_scheme='light')
        await context.add_init_script('localStorage.setItem("unsloth_auth_token", '+json.dumps(session.access_token)+');localStorage.setItem("unsloth_refresh_token", '+json.dumps(session.refresh_token)+');')
        page = await context.new_page()
        errors=[]
        page.on('pageerror', lambda err: errors.append(str(err)))
        def request(req):
            if req.url.endswith('/api/chat/messages:batch'):
                ids = req.post_data_json['threadIds']
                facts['batch_sizes'].append(len(ids))
                facts['batch_contains_oldest'].append(threads[-1]['id'] in ids)
        page.on('request', request)
        await page.goto(session.base_url+'/chat',wait_until='domcontentloaded')
        await page.get_by_role('button',name='Search',exact=True).first.wait_for(timeout=90000)
        await page.get_by_role('button',name='Search',exact=True).first.click()
        query=page.get_by_placeholder('Search chats...')
        await query.wait_for(timeout=30000)
        await page.get_by_role('option',name='Chat 000',exact=False).first.wait_for(timeout=60000)
        await query.fill(TITLE)
        await page.wait_for_timeout(800)
        result=page.get_by_role('option').filter(has_text=TITLE)
        facts['oldest_title_results']=await result.count()
        facts['empty_match_visible']=await page.get_by_text('No chats match.',exact=True).is_visible()
        shot=out_dir/(label.lower()+'_search.png')
        await page.screenshot(path=str(shot))
        assert facts['batch_sizes'] and all(n<=200 for n in facts['batch_sizes']), facts
        assert not any(facts['batch_contains_oldest']),facts
        expected=0 if label.upper()=='BEFORE' else 1
        assert facts['oldest_title_results']==expected,facts
        if expected:
            await result.click()
            await page.wait_for_url('**thread=evidence-thread-200*',timeout=30000)
            await page.get_by_text('Ledger entry 200 quokka',exact=True).first.wait_for(timeout=30000)
            facts['opened_oldest_message']=True
            await page.screenshot(path=str(out_dir/'after_opened.png'))
        else:
            assert facts['empty_match_visible'],facts
            facts['opened_oldest_message']=False
        facts['browser_errors']=errors
        facts['browser_version']=browser.version
        facts['viewport']={'width':1280,'height':900}
        await context.close()
        await browser.close()
    (out_dir/'facts.json').write_text(json.dumps(facts,indent=2)+'\n')
    return [shot],facts
