/**
 * Thorough UI inspection of the SEO Crawler frontend at http://localhost:8000
 * Captures: screenshots, console errors, network failures, and interaction bugs.
 */

import { chromium } from 'playwright';
import { writeFileSync } from 'fs';

const BASE = 'http://localhost:8000';
const SS = 'D:/crawler masivo/tests/screenshots';

// Collectors
const consoleMessages = [];
const networkErrors = [];
const allRequests = [];

(async () => {
  const browser = await chromium.launch({ headless: true });
  const context = await browser.newContext({
    viewport: { width: 1440, height: 900 },
    locale: 'es-ES',
  });
  const page = await context.newPage();

  // ===== COLLECT CONSOLE MESSAGES =====
  page.on('console', msg => {
    consoleMessages.push({
      type: msg.type(),
      text: msg.text(),
      location: msg.location(),
    });
  });

  page.on('pageerror', err => {
    consoleMessages.push({
      type: 'pageerror',
      text: err.message,
      stack: err.stack,
    });
  });

  // ===== COLLECT NETWORK =====
  page.on('requestfailed', req => {
    networkErrors.push({
      url: req.url(),
      method: req.method(),
      failure: req.failure()?.errorText,
      resourceType: req.resourceType(),
    });
  });

  page.on('response', res => {
    allRequests.push({
      url: res.url(),
      status: res.status(),
      method: res.request().method(),
      resourceType: res.request().resourceType(),
    });
  });

  // ===== STEP 1: Navigate to initial page =====
  console.log('\n=== STEP 1: Navigate to initial page ===');
  try {
    await page.goto(BASE, { waitUntil: 'networkidle', timeout: 15000 });
  } catch (e) {
    console.log('WARNING: networkidle timeout, continuing...');
    await page.waitForTimeout(3000);
  }
  await page.screenshot({ path: `${SS}/01-initial-page.png`, fullPage: true });
  console.log('Screenshot: 01-initial-page.png');

  // Wait a bit for Alpine.js to initialize
  await page.waitForTimeout(2000);
  await page.screenshot({ path: `${SS}/02-after-alpine-init.png`, fullPage: true });
  console.log('Screenshot: 02-after-alpine-init.png');

  // ===== STEP 2: Report console errors so far =====
  console.log('\n=== STEP 2: Console messages after initial load ===');
  const initialErrors = consoleMessages.filter(m => m.type === 'error' || m.type === 'pageerror');
  const initialWarnings = consoleMessages.filter(m => m.type === 'warning');
  console.log(`  Errors: ${initialErrors.length}`);
  initialErrors.forEach(e => console.log(`    [${e.type}] ${e.text}`));
  console.log(`  Warnings: ${initialWarnings.length}`);
  initialWarnings.forEach(w => console.log(`    [warning] ${w.text}`));

  // ===== STEP 3: Check network requests =====
  console.log('\n=== STEP 3: Network requests after initial load ===');
  const failedAPI = allRequests.filter(r => r.status >= 400 && r.url.includes('/api/'));
  const failedOther = allRequests.filter(r => r.status >= 400 && !r.url.includes('/api/'));
  console.log(`  Total requests: ${allRequests.length}`);
  console.log(`  Failed API calls (4xx/5xx): ${failedAPI.length}`);
  failedAPI.forEach(r => console.log(`    ${r.method} ${r.url} => ${r.status}`));
  console.log(`  Failed other resources: ${failedOther.length}`);
  failedOther.forEach(r => console.log(`    ${r.method} ${r.url} => ${r.status}`));
  console.log(`  Network errors (no response): ${networkErrors.length}`);
  networkErrors.forEach(r => console.log(`    ${r.method} ${r.url} => ${r.failure}`));

  // ===== STEP 4: Check page content/state =====
  console.log('\n=== STEP 4: Page content analysis ===');
  const pageTitle = await page.title();
  console.log(`  Page title: "${pageTitle}"`);

  // Check if the app element rendered
  const appVisible = await page.locator('.app').isVisible().catch(() => false);
  console.log(`  .app visible: ${appVisible}`);

  const topbarVisible = await page.locator('.topbar').isVisible().catch(() => false);
  console.log(`  .topbar visible: ${topbarVisible}`);

  // Check if jobs loaded or empty state shown
  const tableVisible = await page.locator('table').isVisible().catch(() => false);
  const emptyVisible = await page.locator('.empty').isVisible().catch(() => false);
  const loadingVisible = await page.locator('.loading-overlay').isVisible().catch(() => false);
  const errorBanner = await page.locator('[style*="border-color:var(--red)"]').first().isVisible().catch(() => false);
  console.log(`  Table visible: ${tableVisible}`);
  console.log(`  Empty state visible: ${emptyVisible}`);
  console.log(`  Loading visible: ${loadingVisible}`);
  console.log(`  Error banner visible: ${errorBanner}`);

  if (errorBanner) {
    const errorText = await page.locator('[style*="border-color:var(--red)"]').first().textContent().catch(() => 'N/A');
    console.log(`  Error banner text: "${errorText.trim()}"`);
  }

  // Count job rows
  const jobRows = await page.locator('tbody tr').count().catch(() => 0);
  console.log(`  Job rows in table: ${jobRows}`);

  // ===== STEP 5: Click "+ Nuevo rastreo" =====
  console.log('\n=== STEP 5: Open "Nuevo rastreo" modal ===');
  const createBtn = page.locator('button:has-text("Nuevo rastreo")').first();
  const createBtnVisible = await createBtn.isVisible().catch(() => false);
  console.log(`  "Nuevo rastreo" button visible: ${createBtnVisible}`);

  if (createBtnVisible) {
    await createBtn.click();
    await page.waitForTimeout(500);
    await page.screenshot({ path: `${SS}/03-nuevo-rastreo-modal.png`, fullPage: true });
    console.log('  Screenshot: 03-nuevo-rastreo-modal.png');

    // Check modal elements
    const modalVisible = await page.locator('.modal').isVisible().catch(() => false);
    console.log(`  Modal visible: ${modalVisible}`);

    const formGroups = await page.locator('.modal .form-group').count();
    console.log(`  Form groups in modal: ${formGroups}`);

    // Check inputs are interactive
    const nameInput = page.locator('.modal input[type="text"]').first();
    const nameInputVisible = await nameInput.isVisible().catch(() => false);
    console.log(`  Name input visible: ${nameInputVisible}`);

    // ===== STEP 6: Fill form and submit =====
    console.log('\n=== STEP 6: Fill form with "Test crawl" / "https://example.com" ===');

    // Clear and type name
    await nameInput.fill('Test crawl');
    await page.waitForTimeout(200);

    // Fill seeds textarea
    const seedsTextarea = page.locator('.modal textarea').first();
    await seedsTextarea.fill('https://example.com');
    await page.waitForTimeout(200);

    await page.screenshot({ path: `${SS}/04-form-filled.png`, fullPage: true });
    console.log('  Screenshot: 04-form-filled.png');

    // Reset console/network counters for this step
    const preSubmitConsoleLen = consoleMessages.length;
    const preSubmitNetworkLen = allRequests.length;
    const preSubmitErrorLen = networkErrors.length;

    // Click submit
    const submitBtn = page.locator('.modal button:has-text("Iniciar rastreo")');
    const submitDisabled = await submitBtn.isDisabled().catch(() => null);
    console.log(`  Submit button disabled: ${submitDisabled}`);

    if (!submitDisabled) {
      await submitBtn.click();
      console.log('  Clicked "Iniciar rastreo"');

      // Wait for response
      await page.waitForTimeout(3000);
      await page.screenshot({ path: `${SS}/05-after-submit.png`, fullPage: true });
      console.log('  Screenshot: 05-after-submit.png');

      // Check if modal closed (success) or still open (error)
      const modalStillVisible = await page.locator('.modal').isVisible().catch(() => false);
      console.log(`  Modal still visible after submit: ${modalStillVisible}`);

      if (modalStillVisible) {
        const createError = await page.locator('.modal [style*="border-color:var(--red)"]').textContent().catch(() => null);
        if (createError) {
          console.log(`  Create error message: "${createError.trim()}"`);
        }
        await page.screenshot({ path: `${SS}/05b-submit-error.png`, fullPage: true });
      }

      // New console/network events from submission
      const newConsole = consoleMessages.slice(preSubmitConsoleLen);
      const newNetwork = allRequests.slice(preSubmitNetworkLen);
      const newNetErrors = networkErrors.slice(preSubmitErrorLen);
      const failedSubmitCalls = newNetwork.filter(r => r.status >= 400);

      console.log(`  New console messages: ${newConsole.length}`);
      newConsole.filter(m => m.type === 'error' || m.type === 'pageerror').forEach(e => {
        console.log(`    [${e.type}] ${e.text}`);
      });
      console.log(`  New network requests: ${newNetwork.length}`);
      newNetwork.forEach(r => console.log(`    ${r.method} ${r.url} => ${r.status}`));
      console.log(`  Failed submit calls: ${failedSubmitCalls.length}`);
      failedSubmitCalls.forEach(r => console.log(`    ${r.method} ${r.url} => ${r.status}`));
      console.log(`  New network errors: ${newNetErrors.length}`);
      newNetErrors.forEach(r => console.log(`    ${r.method} ${r.url} => ${r.failure}`));
    }
  }

  // ===== STEP 7: Check if there are jobs to inspect =====
  console.log('\n=== STEP 7: Inspect existing jobs (if any) ===');

  // Close modal if still open
  const modalStillOpen = await page.locator('.modal').isVisible().catch(() => false);
  if (modalStillOpen) {
    await page.locator('.modal-backdrop').click({ position: { x: 10, y: 10 } }).catch(() => {});
    await page.waitForTimeout(500);
  }

  // Reload jobs list
  await page.waitForTimeout(1000);
  const currentJobRows = await page.locator('tbody tr').count().catch(() => 0);
  console.log(`  Job rows visible: ${currentJobRows}`);

  if (currentJobRows > 0) {
    // Click on the first job
    console.log('  Clicking first job row...');
    await page.locator('tbody tr').first().click();
    await page.waitForTimeout(2000);
    await page.screenshot({ path: `${SS}/06-job-detail-overview.png`, fullPage: true });
    console.log('  Screenshot: 06-job-detail-overview.png');

    // Check detail view rendered
    const detailView = await page.locator('h2').first().textContent().catch(() => 'N/A');
    console.log(`  Detail header: "${detailView}"`);

    // Check stats cards
    const statCards = await page.locator('.stat-card').count().catch(() => 0);
    console.log(`  Stat cards visible: ${statCards}`);

    // Check tabs
    const tabs = await page.locator('.tab').count().catch(() => 0);
    console.log(`  Tabs count: ${tabs}`);

    // Check for overview charts
    const barCharts = await page.locator('.bar-chart').count().catch(() => 0);
    console.log(`  Bar charts in overview: ${barCharts}`);

    // ===== TAB: URLs =====
    console.log('\n  --- Tab: URLs ---');
    const urlsTab = page.locator('.tab:has-text("URLs")');
    if (await urlsTab.isVisible().catch(() => false)) {
      await urlsTab.click();
      await page.waitForTimeout(2000);
      await page.screenshot({ path: `${SS}/07-tab-urls.png`, fullPage: true });
      console.log('  Screenshot: 07-tab-urls.png');

      const urlTableVisible = await page.locator('.table-wrap table').isVisible().catch(() => false);
      const urlEmptyVisible = await page.locator('.empty:has-text("No se encontraron URLs")').isVisible().catch(() => false);
      console.log(`    URL table visible: ${urlTableVisible}`);
      console.log(`    URL empty state: ${urlEmptyVisible}`);

      if (urlTableVisible) {
        const urlRowCount = await page.locator('.table-wrap tbody tr').count().catch(() => 0);
        console.log(`    URL rows: ${urlRowCount}`);
      }
    }

    // ===== TAB: Problemas =====
    console.log('\n  --- Tab: Problemas ---');
    const issuesTab = page.locator('.tab:has-text("Problemas")');
    if (await issuesTab.isVisible().catch(() => false)) {
      await issuesTab.click();
      await page.waitForTimeout(2000);
      await page.screenshot({ path: `${SS}/08-tab-problemas.png`, fullPage: true });
      console.log('  Screenshot: 08-tab-problemas.png');

      const issueTableVisible = await page.locator('.table-wrap table').isVisible().catch(() => false);
      const issueEmptyVisible = await page.locator('.empty:has-text("No se encontraron problemas")').isVisible().catch(() => false);
      console.log(`    Issues table visible: ${issueTableVisible}`);
      console.log(`    Issues empty state: ${issueEmptyVisible}`);

      if (issueTableVisible) {
        const issueRowCount = await page.locator('.table-wrap tbody tr').count().catch(() => 0);
        console.log(`    Issue rows: ${issueRowCount}`);
      }
    }

    // ===== TAB: Enlaces =====
    console.log('\n  --- Tab: Enlaces ---');
    const linksTab = page.locator('.tab:has-text("Enlaces")');
    if (await linksTab.isVisible().catch(() => false)) {
      await linksTab.click();
      await page.waitForTimeout(2000);
      await page.screenshot({ path: `${SS}/09-tab-enlaces.png`, fullPage: true });
      console.log('  Screenshot: 09-tab-enlaces.png');

      const linksTableVisible = await page.locator('.table-wrap table').isVisible().catch(() => false);
      const linksEmptyVisible = await page.locator('.empty:has-text("No se encontraron enlaces")').isVisible().catch(() => false);
      console.log(`    Links table visible: ${linksTableVisible}`);
      console.log(`    Links empty state: ${linksEmptyVisible}`);

      if (linksTableVisible) {
        const linkRowCount = await page.locator('.table-wrap tbody tr').count().catch(() => 0);
        console.log(`    Link rows: ${linkRowCount}`);
      }
    }

    // Go back to overview for a final check
    console.log('\n  --- Back to Resumen ---');
    const overviewTab = page.locator('.tab:has-text("Resumen")');
    if (await overviewTab.isVisible().catch(() => false)) {
      await overviewTab.click();
      await page.waitForTimeout(1000);
      await page.screenshot({ path: `${SS}/10-back-to-overview.png`, fullPage: true });
      console.log('  Screenshot: 10-back-to-overview.png');
    }

    // Check back button
    const backBtn = page.locator('button:has-text("Trabajos")');
    if (await backBtn.isVisible().catch(() => false)) {
      await backBtn.click();
      await page.waitForTimeout(1000);
      await page.screenshot({ path: `${SS}/11-back-to-jobs-list.png`, fullPage: true });
      console.log('  Screenshot: 11-back-to-jobs-list.png');
    }
  } else {
    console.log('  No jobs available to inspect detail view.');
    await page.screenshot({ path: `${SS}/06-no-jobs-state.png`, fullPage: true });
    console.log('  Screenshot: 06-no-jobs-state.png');
  }

  // ===== STEP 8: Visual layout checks =====
  console.log('\n=== STEP 8: Visual/Layout checks ===');

  // Check for overlapping elements
  const topbar = await page.locator('.topbar').boundingBox().catch(() => null);
  const main = await page.locator('.main').boundingBox().catch(() => null);
  if (topbar && main) {
    const overlap = topbar.y + topbar.height > main.y;
    console.log(`  Topbar overlaps main: ${overlap}`);
    if (overlap) {
      console.log(`    Topbar bottom: ${topbar.y + topbar.height}, Main top: ${main.y}`);
    }
  }

  // Check for horizontal scrollbar (layout overflow)
  const hasHScroll = await page.evaluate(() => document.documentElement.scrollWidth > document.documentElement.clientWidth);
  console.log(`  Has horizontal scroll (overflow): ${hasHScroll}`);

  // Check for elements extending beyond viewport
  const viewportWidth = await page.evaluate(() => window.innerWidth);
  console.log(`  Viewport width: ${viewportWidth}`);

  // ===== FINAL SUMMARY =====
  console.log('\n========================================');
  console.log('=== FINAL SUMMARY ===');
  console.log('========================================');

  const totalErrors = consoleMessages.filter(m => m.type === 'error' || m.type === 'pageerror');
  const totalWarnings = consoleMessages.filter(m => m.type === 'warning');
  const totalFailedAPI = allRequests.filter(r => r.status >= 400 && r.url.includes('/api/'));
  const totalFailedResources = allRequests.filter(r => r.status >= 400 && !r.url.includes('/api/'));

  console.log(`\nConsole Errors: ${totalErrors.length}`);
  totalErrors.forEach(e => console.log(`  [${e.type}] ${e.text}${e.stack ? '\n    Stack: ' + e.stack.split('\n')[0] : ''}`));

  console.log(`\nConsole Warnings: ${totalWarnings.length}`);
  totalWarnings.forEach(w => console.log(`  [warning] ${w.text}`));

  console.log(`\nAll Console Messages: ${consoleMessages.length}`);
  consoleMessages.forEach(m => console.log(`  [${m.type}] ${m.text.substring(0, 200)}`));

  console.log(`\nFailed API Calls: ${totalFailedAPI.length}`);
  totalFailedAPI.forEach(r => console.log(`  ${r.method} ${r.url} => ${r.status}`));

  console.log(`\nFailed Resource Loads: ${totalFailedResources.length}`);
  totalFailedResources.forEach(r => console.log(`  ${r.method} ${r.url} => ${r.status}`));

  console.log(`\nNetwork Errors (no response): ${networkErrors.length}`);
  networkErrors.forEach(r => console.log(`  ${r.method} ${r.url} => ${r.failure}`));

  console.log(`\nTotal Requests: ${allRequests.length}`);
  const byStatus = {};
  allRequests.forEach(r => { byStatus[r.status] = (byStatus[r.status] || 0) + 1; });
  Object.entries(byStatus).sort().forEach(([s, c]) => console.log(`  HTTP ${s}: ${c} requests`));

  // Save full report as JSON
  const report = {
    timestamp: new Date().toISOString(),
    consoleMessages,
    networkErrors,
    allRequests,
    summary: {
      totalConsoleErrors: totalErrors.length,
      totalConsoleWarnings: totalWarnings.length,
      totalFailedAPICalls: totalFailedAPI.length,
      totalFailedResources: totalFailedResources.length,
      totalNetworkErrors: networkErrors.length,
      totalRequests: allRequests.length,
    }
  };
  writeFileSync(`${SS}/inspection-report.json`, JSON.stringify(report, null, 2));
  console.log('\nFull report saved to inspection-report.json');

  await browser.close();
  console.log('\nInspection complete.');
})();
