// node overflow.js BASE_URL LANG PATH... : per path, the page width at a
// 320px viewport as JSON. The overflow-x: clip on html and body is lifted
// for the measurement, so documentElement.scrollWidth shows any overflow.
const { chromium } = require('playwright');
const [base, lang, ...paths] = process.argv.slice(2);
(async () => {
  const browser = await chromium.launch();
  const page = await (await browser.newContext({ viewport: { width: 320, height: 800 } })).newPage();
  await page.goto(`${base}/_as_admin?lang=${lang}`);
  const out = {};
  for (const path of paths) {
    const resp = await page.goto(base + path, { waitUntil: 'networkidle' });
    await page.addStyleTag({ content: 'html, body { overflow-x: visible !important; }' });
    out[path] = await page.evaluate(status => {
      const wide = [];
      for (const el of document.querySelectorAll('body *')) {
        const r = el.getBoundingClientRect();
        if (r.width && r.right > 320.5 && el.parentElement.getBoundingClientRect().right <= 320.5) {
          wide.push(`${el.tagName.toLowerCase()}.${[...el.classList].join('.')} (right ${Math.round(r.right)})`);
        }
      }
      return { status, html: document.documentElement.scrollWidth, body: document.body.scrollWidth, wide };
    }, resp.status());
  }
  console.log(JSON.stringify(out));
  await browser.close();
})();
