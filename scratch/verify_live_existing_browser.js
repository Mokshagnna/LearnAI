const { chromium } = require('C:/Users/moksh/AppData/Roaming/npm/node_modules/omniroute/node_modules/playwright');
const fs = require('fs');

const PROD_URL = 'https://learnai-fzgd.onrender.com';
const USER_DATA_DIR = 'C:/Users/moksh/.gemini/antigravity/brain/6dbc91e2-991a-4dcf-9161-cd9c9f393fb8/scratch/chrome_user_profile';
const SCREENSHOT_DIR = 'C:/Users/moksh/.gemini/antigravity/brain/6dbc91e2-991a-4dcf-9161-cd9c9f393fb8/scratch';

const TEST_ID = Date.now().toString(36);
const TEST_EMAIL = `testprod_${TEST_ID}@gmail.com`;
const TEST_PASSWORD = 'TestPass123!';
const TEST_NAME = `Test Prod ${TEST_ID}`;

async function run() {
  console.log('=== VERIFYING LIVE PRODUCTION DEPLOYMENT WITH BROWSER PROFILE ===');
  console.log(`URL: ${PROD_URL}`);

  // Launch persistent context so disk cache & cookies persist
  const context = await chromium.launchPersistentContext(USER_DATA_DIR, {
    headless: true,
    executablePath: 'C:/Program Files/Google/Chrome/Application/chrome.exe',
    args: ['--no-sandbox']
  });

  const page = await context.newPage();

  const loadedAssets = [];
  page.on('response', resp => {
    const url = resp.url();
    if (url.includes('app.js') || url.includes('style.css')) {
      loadedAssets.push({ url, status: resp.status(), fromCache: resp.fromServiceWorker() });
      console.log(`[ASSET LOADED] ${url} (HTTP ${resp.status()})`);
    }
  });

  console.log('\nStep 1: Navigating to production...');
  await page.goto(PROD_URL, { waitUntil: 'domcontentloaded' });
  await page.waitForTimeout(2000);

  // Check 1: Verify assets referenced in HTML and actually loaded
  const htmlAssetUrls = await page.evaluate(() => {
    const script = document.querySelector('script[src*="app.js"]');
    const link = document.querySelector('link[href*="style.css"]');
    return {
      scriptSrc: script ? script.getAttribute('src') : null,
      linkHref: link ? link.getAttribute('href') : null
    };
  });
  console.log(`HTML Script src: ${htmlAssetUrls.scriptSrc}`);
  console.log(`HTML Link href: ${htmlAssetUrls.linkHref}`);

  if (!htmlAssetUrls.scriptSrc.includes('v=2.7.0') || !htmlAssetUrls.linkHref.includes('v=2.7.0')) {
    throw new Error(`Production HTML still referencing old assets! scriptSrc=${htmlAssetUrls.scriptSrc}, linkHref=${htmlAssetUrls.linkHref}`);
  }

  // Check 2: Register test student
  console.log('\nStep 2: Registering test student...');
  await page.click('#goToRegisterBtn');
  await page.fill('#registerName', TEST_NAME);
  await page.fill('#registerEmail', TEST_EMAIL);
  await page.fill('#registerPassword', TEST_PASSWORD);
  await page.fill('#registerConfirmPassword', TEST_PASSWORD);
  await page.click('#registerSubmitBtn');

  await page.waitForSelector('#setup:not(.hidden)', { timeout: 30000 });
  console.log('Registered and landed on setup screen.');

  // Check 3: Generate quiz
  console.log('\nStep 3: Generating quiz for 22MT103...');
  await page.selectOption('#course', '22MT103');
  await page.waitForTimeout(1500);

  const topicVal = await page.evaluate(() => {
    const sel = document.getElementById('topic');
    const opt = Array.from(sel.options).find(o => o.value && o.value.trim().length > 0);
    return opt ? opt.value : null;
  });
  console.log(`Selected topic: ${topicVal}`);
  await page.selectOption('#topic', topicVal);
  await page.click('#generateBtn');

  await page.waitForSelector('#quizSection:not(.hidden)', { timeout: 120000 });
  console.log('Quiz loaded and visible in quizSection.');

  // Check 4: Verify Exit Quiz button is NOT in DOM
  const exitBtnInDom = await page.evaluate(() => !!document.getElementById('exitQuizBtn'));
  console.log(`Exit button in DOM? ${exitBtnInDom ? 'YES (FAILURE)' : 'NO (CORRECT)'}`);

  // Check 5: Answer Question 1 (Option B), click Next
  console.log('\nStep 4: Answering Question 1 and advancing...');
  const optionsQ1 = await page.locator('#options .option').all();
  await optionsQ1[1].click(); // Option B
  await page.waitForTimeout(500);

  await page.click('#nextBtn');
  await page.waitForTimeout(500);

  const progressQ2 = await page.locator('#progressText').textContent();
  console.log(`Current progress after Next: ${progressQ2}`);

  // Answer Question 2 (Option C)
  const optionsQ2 = await page.locator('#options .option').all();
  await optionsQ2[2].click(); // Option C
  await page.waitForTimeout(500);

  const quizSessionId = await page.evaluate(() => {
    const raw = localStorage.getItem('learnai_active_quiz');
    const parsed = raw ? JSON.parse(raw) : null;
    return parsed?.currentQuiz?.session_id;
  });
  console.log(`Active session ID: ${quizSessionId}`);

  // Check 6: Navigate to Dashboard, then Practice
  console.log('\nStep 5: Practice -> Dashboard -> Practice...');
  await page.click('#navDashboardBtn');
  await page.waitForTimeout(2000);
  const isDashboard = await page.evaluate(() => !document.getElementById('dashboardSection').classList.contains('hidden'));
  console.log(`Navigated to Dashboard: ${isDashboard}`);

  await page.click('#navPracticeBtn');
  await page.waitForTimeout(2000);
  await page.screenshot({ path: `${SCREENSHOT_DIR}/post_deploy_dash_return.png` });

  const isQuizAfterDash = await page.evaluate(() => !document.getElementById('quizSection').classList.contains('hidden'));
  const progressAfterDash = await page.locator('#progressText').textContent();
  const selectedAfterDash = await page.evaluate(() => {
    const sel = document.querySelector('#options .option.selected .option-letter');
    return sel ? sel.textContent.trim() : 'NONE';
  });
  console.log(`Restored quizSection after Dashboard? ${isQuizAfterDash}`);
  console.log(`Progress: ${progressAfterDash} (expected Question 2 of 10)`);
  console.log(`Selected answer: ${selectedAfterDash} (expected C.)`);

  // Check 7: Navigate to Notes & Slides, then Practice
  console.log('\nStep 6: Practice -> Notes & Slides -> Practice...');
  await page.click('#navNotesBtn');
  await page.waitForTimeout(2000);
  const isNotes = await page.evaluate(() => !document.getElementById('notesUploadSection').classList.contains('hidden'));
  console.log(`Navigated to Notes: ${isNotes}`);

  await page.click('#navPracticeBtn');
  await page.waitForTimeout(2000);
  await page.screenshot({ path: `${SCREENSHOT_DIR}/post_deploy_notes_return.png` });

  const isQuizAfterNotes = await page.evaluate(() => !document.getElementById('quizSection').classList.contains('hidden'));
  const progressAfterNotes = await page.locator('#progressText').textContent();
  const selectedAfterNotes = await page.evaluate(() => {
    const sel = document.querySelector('#options .option.selected .option-letter');
    return sel ? sel.textContent.trim() : 'NONE';
  });
  console.log(`Restored quizSection after Notes? ${isQuizAfterNotes}`);
  console.log(`Progress: ${progressAfterNotes} (expected Question 2 of 10)`);
  console.log(`Selected answer: ${selectedAfterNotes} (expected C.)`);

  // Check 8: Browser refresh
  console.log('\nStep 7: Browser reload (F5)...');
  await page.reload({ waitUntil: 'domcontentloaded' });
  await page.waitForTimeout(3000);
  await page.screenshot({ path: `${SCREENSHOT_DIR}/post_deploy_refresh.png` });

  const isQuizAfterReload = await page.evaluate(() => !document.getElementById('quizSection').classList.contains('hidden'));
  const progressAfterReload = await page.locator('#progressText').textContent();
  const selectedAfterReload = await page.evaluate(() => {
    const sel = document.querySelector('#options .option.selected .option-letter');
    return sel ? sel.textContent.trim() : 'NONE';
  });
  console.log(`Restored quizSection after Reload? ${isQuizAfterReload}`);
  console.log(`Progress: ${progressAfterReload} (expected Question 2 of 10)`);
  console.log(`Selected answer: ${selectedAfterReload} (expected C.)`);

  // Final verification summary
  const allPassed = (
    htmlAssetUrls.scriptSrc.includes('v=2.7.0') &&
    htmlAssetUrls.linkHref.includes('v=2.7.0') &&
    !exitBtnInDom &&
    isQuizAfterDash && progressAfterDash.includes('2') && selectedAfterDash === 'C.' &&
    isQuizAfterNotes && progressAfterNotes.includes('2') && selectedAfterNotes === 'C.' &&
    isQuizAfterReload && progressAfterReload.includes('2') && selectedAfterReload === 'C.'
  );

  console.log('\n========================================');
  console.log(`LIVE PRODUCTION VERIFICATION: ${allPassed ? 'ALL PASSED' : 'FAILED'}`);
  console.log('========================================');

  await context.close();
}

run().catch(e => {
  console.error('Fatal error during verification:', e);
  process.exit(1);
});

