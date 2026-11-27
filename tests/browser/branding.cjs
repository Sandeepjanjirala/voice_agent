const {chromium}=require('playwright');
const assert=require('node:assert/strict');
(async()=>{
 const browser=await chromium.launch({headless:true});
 try {
 const page=await browser.newPage({viewport:{width:1440,height:1080}});
 const errors=[]; page.on('pageerror',e=>errors.push(e.message));
 await page.goto('http://127.0.0.1:8767/accounts/login/');
 await page.locator('[name=username]').fill('dashboard-review');
 await page.locator('[name=password]').fill('local-disposable-dashboard-check');
 await page.getByRole('button',{name:'Sign in',exact:true}).click();
 await page.waitForURL('**/home/');
 assert.match(await page.title(),/Vaani/);
 assert.doesNotMatch(await page.locator('body').innerText(),/Sri Chaitanya/i);
 assert.equal(await page.locator('.school-logo').count(),0);
 assert.ok((await page.locator('.cosmic-hero').boundingBox()).height>=360,'The animated banner stays enlarged');
 await page.waitForTimeout(1300);
 assert.equal(await page.locator('.cosmic-stars').evaluate(el=>getComputedStyle(el).animationName),'star-drift');
 assert.equal(await page.locator('.cosmic-hero').evaluate(el=>getComputedStyle(el,'::before').animationName),'beam-drift');
 assert.equal(await page.locator('.cosmic-hero').evaluate(el=>getComputedStyle(el,'::after').animationName),'horizon-glow');
 await page.locator('#motion-toggle').click();
 assert.equal(await page.locator('#motion-toggle').getAttribute('aria-pressed'),'true');
 assert.equal(await page.locator('.cosmic-stars').evaluate(el=>getComputedStyle(el).animationPlayState),'paused');
 await page.locator('#motion-toggle').click();
 assert.equal(await page.locator('.cosmic-stars').evaluate(el=>getComputedStyle(el).animationPlayState),'running');
 await page.locator('#call-search').fill('no matching number');
 await page.locator('#no-call-matches').waitFor({state:'visible'});
 await page.locator('#setup-checklist summary').click();
 let refreshRequests=0;
 page.on('request',request=>{
  if(request.url()==='http://127.0.0.1:8767/home/'&&['xhr','fetch'].includes(request.resourceType()))refreshRequests++;
 });
 await page.locator('#refresh-home').click();
 await page.waitForFunction(()=>document.querySelector('#refresh-status').textContent.startsWith('Updated'));
 assert.equal(refreshRequests,1,'One refresh must send exactly one request');
 assert.equal(await page.locator('#call-search').inputValue(),'no matching number');
 assert.equal(await page.locator('#setup-checklist').getAttribute('open'),null);
 await page.locator('#call-search').fill('');
 await page.locator('#setup-checklist summary').click();
 await page.evaluate(()=>window.scrollTo(0,0));
 await page.waitForTimeout(900);
 await page.screenshot({path:'browser-artifacts/vaani-desktop.png',fullPage:true});
 await page.route('**/home/',route=>route.abort());
 await page.locator('#refresh-home').click();
 await page.waitForFunction(()=>document.querySelector('#refresh-status').textContent.includes('Could not update'));
 await page.unroute('**/home/');
 await page.route('**/home/',async route=>{
  const response=await route.fetch();
  const html=await response.text();
  // Scripts in refreshed HTML must never be evaluated by jQuery insertion.
  const body=html.replace(/(id="home-live"[^>]*>)/,'$1<script>window.refreshScriptExecuted=true;</script>');
  assert.notEqual(body,html);
  await route.fulfill({response,body});
 });
 await page.locator('#refresh-home').click();
 await page.waitForFunction(()=>document.querySelector('#refresh-status').textContent.startsWith('Updated'));
 assert.equal(await page.evaluate(()=>window.refreshScriptExecuted),undefined);
 await page.unroute('**/home/');
 for(const width of [390,768,1440]){
 await page.setViewportSize({width,height:1000});
 assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),`Overflow at ${width}`);
 }
 await page.setViewportSize({width:390,height:844});
 assert.ok((await page.locator('.cosmic-hero').boundingBox()).height>=360,'Mobile keeps the larger motion banner');
 await page.locator('#menu-button').click();
 assert.equal(await page.locator('#menu-button').getAttribute('aria-expanded'),'true');
 await page.keyboard.press('Escape');
 assert.equal(await page.locator('#menu-button').getAttribute('aria-expanded'),'false');
 await page.screenshot({path:'browser-artifacts/vaani-mobile.png',fullPage:true});
 await page.emulateMedia({reducedMotion:'reduce'});
 assert.equal(await page.locator('.cosmic-stars').evaluate(el=>getComputedStyle(el).animationName),'none');
 await page.setViewportSize({width:1440,height:1000});
 for (const path of ['/employees/','/make-calls/','/settings/']) {
   const response=await page.goto('http://127.0.0.1:8767'+path);
   assert.equal(response.status(),200);
   assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));
 }
 assert.deepEqual(errors,[]);
 console.log('Vaani browser checks passed: branding, refresh, filtering, error state, preserved controls, mobile navigation and responsive widths.');
 } finally {await browser.close();}
})().catch(e=>{console.error(e);process.exit(1)});
