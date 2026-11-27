const {chromium}=require('playwright');
const assert=require('node:assert/strict');
(async()=>{
 const browser=await chromium.launch({headless:true});
 try {
 const page=await browser.newPage({viewport:{width:1440,height:1000}});
 const errors=[];page.on('pageerror',e=>errors.push(e.message));
 await page.goto('http://127.0.0.1:8767/accounts/login/');
 await page.locator('[name=username]').fill('dashboard-review');
 await page.locator('[name=password]').fill('local-disposable-dashboard-check');
 await page.getByRole('button',{name:'Sign in',exact:true}).click();
 await page.waitForURL('**/home/');
 const dependency=await page.evaluate(()=>({
  version:window.jQuery?.fn.jquery,
  ajax:typeof window.jQuery?.ajax,
  scripts:[...document.scripts].map(script=>script.src).filter(Boolean)
 }));
 assert.equal(dependency.version,'4.1.0');
 assert.equal(dependency.ajax,'function');
 const jqueryScripts=dependency.scripts.filter(src=>src.includes('/vendor/jquery/'));
 assert.equal(jqueryScripts.length,1);
 assert.equal(new URL(jqueryScripts[0]).origin,'http://127.0.0.1:8767');
 assert.ok(dependency.scripts.indexOf(jqueryScripts[0])<dependency.scripts.findIndex(src=>src.endsWith('/dashboard/assistant.js')));
 const panel=page.locator('#assist-panel');
 assert.equal(await panel.isVisible(),false);
 assert.equal(await page.locator('#assist-launcher').isVisible(),true);
 for(const opener of ['.hero-actions [data-open-assist]','.side-helper [data-open-assist]']){
  await page.locator(opener).click();
  assert.equal(await panel.isVisible(),true);
  await page.locator('#assist-close').click();
  assert.equal(await page.locator(opener).evaluate(el=>el===document.activeElement),true);
 }
 await page.locator('#assist-launcher').click();
 assert.equal(await panel.isVisible(),true);
 assert.equal(await page.locator('#assist-input').evaluate(el=>el===document.activeElement),true);
 assert.equal(await panel.evaluate(el=>el.matches(':modal')),false);
 assert.equal(await panel.getAttribute('aria-modal'),'false');
 assert.equal(await page.locator('.page').evaluate(el=>el.inert),false);
 const theme=page.locator('[data-theme-toggle]');
 const originalTheme=await theme.getAttribute('aria-pressed');
 await theme.click();
 assert.notEqual(await theme.getAttribute('aria-pressed'),originalTheme);
 assert.equal(await panel.isVisible(),true);
 await theme.click();
 // Closing help from elsewhere in the workspace must preserve keyboard focus.
 await page.keyboard.press('Escape');
 await page.waitForFunction(()=>!document.querySelector('#assist-panel').open);
 assert.equal(await theme.evaluate(el=>el===document.activeElement),true);
 await page.locator('#assist-launcher').click();
 // The assistant does not trap keyboard navigation inside the conversation.
 await page.locator('#assist-close').focus();
 await page.keyboard.press('Shift+Tab');
 assert.equal(await panel.evaluate(el=>el.contains(document.activeElement)),false);
 await page.locator('#assist-input').focus();
 const checkLayout=async()=>{
  await panel.evaluate(el=>Promise.all(el.getAnimations().map(animation=>animation.finished)));
  const layout=await page.evaluate(()=>{
   const panel=document.querySelector('#assist-panel').getBoundingClientRect();
   const workspace=document.querySelector('.page').getBoundingClientRect();
   return {width:innerWidth,height:innerHeight,scrollWidth:document.documentElement.scrollWidth,
    panel:{left:panel.left,right:panel.right,top:panel.top,bottom:panel.bottom},workspaceRight:workspace.right};
  });
  assert.ok(layout.scrollWidth<=layout.width,`No horizontal overflow at ${layout.width}px`);
  assert.ok(layout.panel.left>=0&&layout.panel.right<=layout.width,`Panel fits ${layout.width}px viewport`);
  assert.ok(layout.panel.top>=0&&layout.panel.bottom<=layout.height,`Panel fits ${layout.height}px height`);
  if(layout.width>=1200){
   assert.ok(layout.workspaceRight<=layout.panel.left,`Workspace does not sit underneath assistant at ${layout.width}px`);
  }else{
   assert.ok(layout.panel.top>=layout.height*.25,`Bottom panel leaves the dashboard visible at ${layout.width}px`);
  }
 };
 await checkLayout();
 const sent=[];
 await page.route('**/api/assistant/chat/',async route=>{
  sent.push(route.request().postDataJSON());
  assert.ok(route.request().headers()['x-csrftoken']);
  await new Promise(resolve=>setTimeout(resolve,250));
  await route.fulfill({contentType:'application/json',body:JSON.stringify({reply:'Hello! Open AI employees to get started. <img src=x onerror=alert(1)>'})});
 });
 await page.locator('#assist-input').fill('Still composing');
 await page.locator('#assist-input').dispatchEvent('keydown',{key:'Enter',isComposing:true});
 assert.equal(await page.locator('#assist-input').inputValue(),'Still composing');
 assert.equal(await page.locator('.assist-message').count(),0);
 assert.equal(sent.length,0);
 await page.locator('[data-assist-prompt]').first().click();
 await page.getByText('Thinking…',{exact:true}).waitFor();
 await page.locator('.assist-model').waitFor();
 assert.equal(sent.length,1);
 assert.equal(await page.locator('.assist-model img').count(),0);
 await page.locator('#assist-input').fill('Can you help in Telugu?');
 await page.locator('#assist-input').press('Enter');
 await page.waitForFunction(()=>document.querySelectorAll('.assist-model').length===2);
 assert.equal(sent[1].history.length,2);
 assert.equal(sent.length,2);
 await page.screenshot({path:'browser-artifacts/assistant-desktop.png',fullPage:true});
 await page.keyboard.press('Escape');
 await page.waitForFunction(()=>document.querySelector('#assist-launcher').getAttribute('aria-expanded')==='false');
 assert.equal(await panel.isVisible(),false);
 assert.equal(await page.locator('#assist-launcher').getAttribute('aria-expanded'),'false');
 await page.locator('#assist-launcher').click();
 assert.equal(await page.locator('.assist-model').count(),2);
 await page.locator('#assist-clear').click();
 assert.equal(await page.locator('.assist-model').count(),0);
 await page.unroute('**/api/assistant/chat/');
 await page.route('**/api/assistant/chat/',route=>route.fulfill({status:502,contentType:'application/json',body:JSON.stringify({error:'The AI service is unavailable. Please try again shortly.'})}));
 await page.locator('#assist-input').fill('Keep this for retry');
 await page.locator('#assist-send').click();
 await page.waitForFunction(()=>document.querySelector('#assist-status').textContent.includes('unavailable'));
 assert.equal(await page.locator('#assist-input').inputValue(),'Keep this for retry');
 assert.equal(await page.locator('#assist-send').isEnabled(),true);
 assert.equal(await page.locator('#assist-welcome').isVisible(),true);
 await page.locator('#assist-clear').click();
 await page.unroute('**/api/assistant/chat/');
 await page.route('**/api/assistant/chat/',route=>route.fulfill({contentType:'text/html',body:'<html>Sign in again</html>'}));
 await page.locator('#assist-input').fill('Keep this after an expired session');
 await page.locator('#assist-send').click();
 await page.getByText('Your session may have expired. Refresh the page and try again.',{exact:true}).waitFor();
 assert.equal(await page.locator('#assist-input').inputValue(),'Keep this after an expired session');
 assert.equal(await page.locator('#assist-send').isEnabled(),true);
 await page.locator('#assist-clear').click();
 // Exercise the configured jQuery deadline without waiting 45 real seconds.
 await page.evaluate(()=>jQuery.ajaxPrefilter(options=>{if(options.url.endsWith('/api/assistant/chat/'))options.timeout=50;}));
 await page.unroute('**/api/assistant/chat/');
 await page.route('**/api/assistant/chat/',async route=>{
  await new Promise(resolve=>setTimeout(resolve,200));
  await route.fulfill({contentType:'application/json',body:JSON.stringify({reply:'Late reply'})}).catch(()=>{});
 });
 await page.locator('#assist-input').fill('Keep this after a timeout');
 await page.locator('#assist-send').click();
 await page.getByText('The reply took too long. Your message is ready to retry.',{exact:true}).waitFor();
 assert.equal(await page.locator('#assist-input').inputValue(),'Keep this after a timeout');
 assert.equal(await page.locator('#assist-send').isEnabled(),true);
 assert.equal(await page.locator('.assist-message').count(),0);
 await page.locator('#assist-clear').click();
 for(const width of [1200,1199,1100,900,650]){
  await page.setViewportSize({width,height:844});
  await checkLayout();
 }
 await page.setViewportSize({width:390,height:844});
 await checkLayout();
 await page.evaluate(()=>window.scrollTo(0,document.documentElement.scrollHeight));
 const footer=await page.locator('.page>footer').boundingBox();
 const bounds=await panel.boundingBox();
 assert.ok(footer.y+footer.height<=bounds.y,'Bottom dashboard content scrolls above assistant');
 await page.screenshot({path:'browser-artifacts/assistant-mobile.png',fullPage:true});
 await page.setViewportSize({width:390,height:480});
 await checkLayout();
 // Short windows may scroll the panel, but every composer control remains reachable.
 await page.locator('#assist-input').click();
 assert.equal(await page.locator('#assist-input').evaluate(el=>el===document.activeElement),true);
 await page.locator('#assist-send').click({trial:true});
 await page.locator('#assist-close').click();
 await page.waitForFunction(()=>document.activeElement.id==='assist-launcher');
 assert.equal(await page.locator('#assist-launcher').evaluate(el=>el===document.activeElement),true);
 await page.reload();
 assert.equal(await panel.isVisible(),false);
 assert.deepEqual(errors,[]);
 console.log('Assistant browser checks passed: nonmodal dashboard interaction, keyboard focus, responsive docking, mobile/short-height reachability, local jQuery, single sends, IME composition, history, safe text rendering, error/timeout recovery and clear chat.');
 }finally{await browser.close();}
})().catch(e=>{console.error(e);process.exit(1)});
