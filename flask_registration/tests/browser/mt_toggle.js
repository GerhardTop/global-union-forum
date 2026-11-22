// node mt_toggle.js BASE_URL : the machine-translation label (_mt.html) in a
// real browser, as JSON. 1) A visitor reading /vraagstuk/1 in English swaps
// the issue title to the Dutch original and back. 2) A logged-in reader in
// German with "always translate" gets a post translated at once (the
// translate route is stubbed) and can swap it back.
const { chromium } = require('playwright');
const [base] = process.argv.slice(2);

async function state(box) {
  return box.evaluate(b => {
    const tr = b.querySelector('[data-mt-translation]'), orig = b.querySelector('[data-mt-original]');
    const btn = b.querySelector('.gu-mt__toggle');
    const visible = tr && !tr.hidden ? tr : orig;
    return {
      visibleLang: visible.getAttribute('lang'), visibleText: visible.textContent.trim().slice(0, 60),
      hasTranslation: !!tr, label: b.querySelector('[data-mt-label]').textContent.trim(),
      button: btn && btn.textContent.trim(), pressed: btn && btn.getAttribute('aria-pressed'),
    };
  });
}

(async () => {
  const browser = await chromium.launch();
  const out = {};

  const visitor = await (await browser.newContext()).newPage();
  await visitor.goto(`${base}/lang/en`);
  await visitor.goto(`${base}/vraagstuk/1`, { waitUntil: 'networkidle' });
  const title = visitor.locator('.gu-cockpit__titlerow [data-mt]').first();
  out.title = [await state(title)];
  await title.locator('.gu-mt__toggle').click();
  out.title.push(await state(title));
  await title.locator('.gu-mt__toggle').press('Enter');   // a real button: the keyboard works too
  out.title.push(await state(title));

  const reader = await (await browser.newContext()).newPage();
  await reader.route('**/forum/translate/**', route => route.fulfill({
    contentType: 'application/json', body: JSON.stringify({ translation: 'Erster Absatz.\n\nZweiter Absatz.' }),
  }));
  await reader.goto(`${base}/_as_admin?lang=de`);
  await reader.goto(`${base}/forum/1`, { waitUntil: 'networkidle' });
  const post = reader.locator('.gu-post [data-mt]').first();
  await post.locator('[data-mt-translation]').waitFor({ state: 'attached', timeout: 5000 });
  out.post = [await state(post)];
  out.postParagraphs = await post.locator('[data-mt-translation] p').count();
  await post.locator('.gu-mt__toggle').click();
  out.post.push(await state(post));

  console.log(JSON.stringify(out));
  await browser.close();
})();
