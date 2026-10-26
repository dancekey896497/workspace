// Local browser acceptance for this static artifact only. Does not connect to servers.
const fs = require('fs');
const path = require('path');
const os = require('os');
const assert = require('assert/strict');
const {pathToFileURL} = require('url');
const {chromium} = require('C:/Users/luv_l/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright');

(async () => {
  const root = __dirname;
  const read = name => JSON.parse(fs.readFileSync(path.join(root,name),'utf8'));
  const data = read('report-data.json');
  const source = read('source_code.json');
  const commands = read('baseline_commands.json');
  const html = path.join(root,'PP与LMCache_机制可视化报告.html');
  const errors = [], external = [], checks = [];
  const browser = await chromium.launch({executablePath:'C:/Program Files/Google/Chrome/Application/chrome.exe',headless:true,args:['--disable-gpu']});
  try {
    const page = await browser.newPage({viewport:{width:1600,height:1050},deviceScaleFactor:1});
    page.on('pageerror',e=>errors.push(e.message));
    page.on('request',r=>{if(/^https?:/.test(r.url()))external.push(r.url());});
    await page.goto(pathToFileURL(html).href);
    await page.locator('#metric-matrix tbody tr').first().waitFor();
    assert.equal(await page.locator('#metric-matrix tbody tr').count(),13);
    for (const tier of ['0','1','2']) {
      await page.selectOption('#tier-select',tier);
      const pairs=['PP2','PP1'].map(layout=>data.historical_three_tier_pairs.filter(x=>x.layout===layout)[Number(tier)]);
      for(const key of ['ttft_s','tpot_ms','e2el_s','decode_s']){
        const index=data.historical_c4_matrix.metrics.findIndex(x=>x.key===key);
        const shown=await page.locator(`#metric-matrix tbody tr:nth-child(${index+1}) td`).allTextContents();
        const expected=pairs.flatMap(p=>{const m=p.metrics.find(x=>x.key===key);return [m.off_mean,m.on_mean];});
        shown.forEach((text,i)=>assert.ok(Math.abs(Number(text.replaceAll(',',''))-expected[i])<0.00051));
      }
      const hits=await page.locator('#metric-matrix tbody tr:nth-child(11) td').allTextContents();
      assert.deepEqual(hits,['未启用',`${pairs[0].on_hit_percent.toFixed(2)}%`,'未启用',`${pairs[1].on_hit_percent.toFixed(2)}%`]);
      assert.match(await page.locator('#result-method').textContent(),/两个全命中请求/);
      for(const metric of ['ttft','tpot','e2el']){
        await page.selectOption('#metric-select',metric);
        assert.equal(await page.locator('#metric-chart rect').count(),4);
      }
    }
    checks.push('三档四列正式指标、单位、命中率与JSON逐值一致；三种图指标切换正常');
    for(const hit of ['partial','miss','full']){
      await page.check(`input[name=hit][value=${hit}]`);
      assert.equal(await page.locator('#flow-on [data-flow]').count(),11);
      await page.locator('#flow-on [data-flow="4"]').click();
      assert.match(await page.locator('#flow-explanation').innerText(),/前41层计算/);
    }
    await page.locator('#flow-on [data-flow="0"]').click();
    assert.ok(await page.locator('#flow-prev').isDisabled());
    await page.locator('#flow-next').click();
    assert.match(await page.locator('#flow-count').innerText(),/2 \/ 11/);
    checks.push('三种命中情景、逐步导航与解释正常');
    for(let i=0;i<6;i++){
      await page.locator(`[data-time="${i}"]`).click();
      assert.equal(await page.locator('#timeline-step').inputValue(),String(i));
      assert.match(await page.locator('#timeline-explain').innerText(),new RegExp(`${i+1} / 6`));
    }
    for(const s of source.snippets){
      await page.locator(`[data-code="${s.id}"]`).click();
      assert.match(await page.locator('#code-content').innerText(),new RegExp(s.title.replace(/[.*+?^${}()|[\]\\]/g,'\\$&')));
      const displayed=await page.locator('#code-content .code-line .src').allTextContents();
      const expected=s.fragments.flatMap(f=>f.code.replace(/\n$/,'').split('\n'));
      assert.deepEqual(displayed,expected);
    }
    checks.push('六步时间线可用；六组源码逐行展示与原摘录完全一致');
    const renderedCommands=await page.locator('#baseline-commands pre code').allTextContents();
    const expectedCommands=commands.items.filter(c=>c.command).map(c=>`${c.environment_text||''}${c.environment_text?'\n':''}${c.command}`);
    assert.deepEqual(renderedCommands,expectedCommands);
    assert.equal(await page.locator('#baseline-commands [data-copy]').count(),6);
    checks.push('P ON、缓存与四D环境和命令完整一致；原OFF仍指向实际档案');
    assert.equal(await page.locator('#sfa-chart rect').count(),2);
    assert.equal(await page.locator('#restore-chart rect').count(),4);
    assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth+1),false);
    await page.selectOption('#tier-select','1');
    await page.selectOption('#metric-select','ttft');
    await page.evaluate(()=>{document.documentElement.style.scrollBehavior='auto';window.scrollTo({top:0,behavior:'instant'});});
    await page.waitForTimeout(160);
    const overview=path.join(os.tmpdir(),'ks-pp-report-overview.png');
    const timeline=path.join(os.tmpdir(),'ks-pp-report-timeline.png');
    const code=path.join(os.tmpdir(),'ks-pp-report-code.png');
    await page.screenshot({path:overview});
    await page.locator('#timeline-chart').screenshot({path:timeline});
    await page.locator('[data-code="none_gate"]').click();
    await page.locator('.code-browser').screenshot({path:code});
    await page.setViewportSize({width:390,height:844});
    await page.evaluate(()=>window.scrollTo(0,0));
    await page.waitForTimeout(180);
    assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth+1),false);
    const mobile=path.join(os.tmpdir(),'ks-pp-report-mobile.png');
    await page.screenshot({path:mobile});
    assert.ok(await page.locator('#metric-chart svg').count());
    const screenshots=[overview,timeline,code,mobile];
    assert.deepEqual(errors,[]);
    assert.deepEqual(external,[]);
    checks.push('1600px与390px页面无整体横向溢出；无JS异常、无网络资源请求');
    const receipt={status:'PASS',artifact:html,checks,errors,external_network_requests:external,screenshots,scope:'只验证本地网页，不运行或验收任何推理服务、硬件性能或候选补丁'};
    fs.writeFileSync(path.join(root,'browser-verification.json'),JSON.stringify(receipt,null,2)+'\n','utf8');
    console.log(JSON.stringify(receipt,null,2));
  } finally {await browser.close();}
})().catch(e=>{console.error(e);process.exitCode=1;});
