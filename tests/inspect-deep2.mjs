/**
 * Deep inspection round 2: fixed locators for Alpine.js template tags.
 */

import { chromium } from 'playwright';

const BASE = 'http://localhost:8000';
const SS = 'D:/crawler masivo/tests/screenshots';

const consoleMessages = [];

(async () => {
  const browser = await chromium.launch({ headless: true });
  const context = await browser.newContext({
    viewport: { width: 1440, height: 900 },
    locale: 'es-ES',
  });
  const page = await context.newPage();

  page.on('console', msg => {
    consoleMessages.push({ type: msg.type(), text: msg.text() });
  });
  page.on('pageerror', err => {
    consoleMessages.push({ type: 'pageerror', text: err.message, stack: err.stack });
  });

  await page.goto(BASE, { waitUntil: 'networkidle', timeout: 15000 }).catch(() => {});
  await page.waitForTimeout(2000);

  // Navigate to e-quironsalud job
  const eqRow = page.locator('tbody tr:has-text("e-quironsalud")');
  if (await eqRow.isVisible().catch(() => false)) {
    await eqRow.click();
    await page.waitForTimeout(2000);

    // ===== URLs tab =====
    console.log('\n=== URLs tab with real data ===');
    await page.locator('.tab:has-text("URLs")').click();
    await page.waitForTimeout(2000);

    const urlRows = await page.locator('table tbody tr').count().catch(() => 0);
    console.log(`URL rows visible: ${urlRows}`);

    if (urlRows > 0) {
      // Get first row data
      const firstRow = page.locator('table tbody tr').first();
      const cells = await firstRow.locator('td').allTextContents();
      console.log(`First row cells: ${cells.map(c => c.trim().substring(0, 50)).join(' | ')}`);
    }

    // Check pagination
    const pagText = await page.locator('.pagination span').first().textContent().catch(() => 'N/A');
    console.log(`Pagination text: "${pagText.trim()}"`);

    // Test page 2
    const nextBtn = page.locator('.pagination-buttons button').last();
    const nextDisabled = await nextBtn.isDisabled().catch(() => true);
    console.log(`Next page button disabled: ${nextDisabled}`);

    if (!nextDisabled) {
      await nextBtn.click();
      await page.waitForTimeout(2000);
      await page.screenshot({ path: `${SS}/deep2-01-urls-page2.png`, fullPage: true });
      console.log('Screenshot: deep2-01-urls-page2.png');
      const page2Rows = await page.locator('table tbody tr').count().catch(() => 0);
      console.log(`Page 2 URL rows: ${page2Rows}`);
      const pag2Text = await page.locator('.pagination span').first().textContent().catch(() => 'N/A');
      console.log(`Page 2 pagination: "${pag2Text.trim()}"`);
    }

    // ===== Filter by internal =====
    console.log('\n=== URL filters ===');
    const internalSelect = page.locator('select').nth(1);
    await internalSelect.selectOption('true');
    await page.waitForTimeout(2000);
    const internalRows = await page.locator('table tbody tr').count().catch(() => 0);
    console.log(`Internal URLs rows: ${internalRows}`);
    await page.screenshot({ path: `${SS}/deep2-02-urls-internal.png`, fullPage: true });

    // Reset
    await internalSelect.selectOption('');
    await page.waitForTimeout(1500);

    // ===== Problemas tab =====
    console.log('\n=== Problemas tab ===');
    await page.locator('.tab:has-text("Problemas")').click();
    await page.waitForTimeout(2000);
    await page.screenshot({ path: `${SS}/deep2-03-issues.png`, fullPage: true });

    const issueRows = await page.locator('table tbody tr').count().catch(() => 0);
    console.log(`Issue rows: ${issueRows}`);

    if (issueRows > 0) {
      for (let i = 0; i < Math.min(issueRows, 5); i++) {
        const row = page.locator('table tbody tr').nth(i);
        const cells = await row.locator('td').allTextContents();
        console.log(`  Issue ${i}: ${cells.map(c => c.trim().substring(0, 40)).join(' | ')}`);
      }
    }

    // Check issue pagination
    const issuePag = await page.locator('.pagination span').first().textContent().catch(() => 'N/A');
    console.log(`Issue pagination: "${issuePag.trim()}"`);

    // Test severity filter
    const severitySelect = page.locator('select').first();
    await severitySelect.selectOption('error');
    await page.waitForTimeout(2000);
    const errorRows = await page.locator('table tbody tr').count().catch(() => 0);
    console.log(`Rows with 'error' severity: ${errorRows}`);
    await page.screenshot({ path: `${SS}/deep2-04-issues-error-filter.png`, fullPage: true });

    await severitySelect.selectOption('');
    await page.waitForTimeout(1000);

    // ===== Enlaces tab =====
    console.log('\n=== Enlaces tab ===');
    await page.locator('.tab:has-text("Enlaces")').click();
    await page.waitForTimeout(2000);
    await page.screenshot({ path: `${SS}/deep2-05-links.png`, fullPage: true });

    const linkRows = await page.locator('table tbody tr').count().catch(() => 0);
    const linksEmpty = await page.locator('.empty').isVisible().catch(() => false);
    console.log(`Link rows: ${linkRows}, empty state: ${linksEmpty}`);

    if (linkRows > 0) {
      for (let i = 0; i < Math.min(linkRows, 3); i++) {
        const row = page.locator('table tbody tr').nth(i);
        const cells = await row.locator('td').allTextContents();
        console.log(`  Link ${i}: ${cells.map(c => c.trim().substring(0, 40)).join(' | ')}`);
      }
    }

    // Go back
    await page.locator('button:has-text("Trabajos")').click();
    await page.waitForTimeout(1000);
  }

  // ===== Mobile viewport test =====
  console.log('\n=== Mobile viewport (375x667) ===');
  await page.setViewportSize({ width: 375, height: 667 });
  await page.waitForTimeout(500);
  await page.screenshot({ path: `${SS}/deep2-06-mobile-jobs.png`, fullPage: true });

  const mobileOverflow = await page.evaluate(() =>
    document.documentElement.scrollWidth > document.documentElement.clientWidth
  );
  console.log(`Mobile has horizontal overflow: ${mobileOverflow}`);

  // Check topbar on mobile
  const topbarBox = await page.locator('.topbar').boundingBox();
  console.log(`Topbar on mobile: w=${topbarBox?.width} h=${topbarBox?.height}`);

  // Check if filter buttons wrap
  const filterBtns = await page.locator('.flex.items-center.gap-1 button').count();
  console.log(`Filter buttons: ${filterBtns}`);

  // ===== Modal on mobile =====
  console.log('\n=== Modal on mobile ===');
  await page.locator('button:has-text("Nuevo rastreo")').first().click();
  await page.waitForTimeout(500);
  await page.screenshot({ path: `${SS}/deep2-07-modal-mobile.png`, fullPage: true });

  // Scroll modal to bottom
  const modal = page.locator('.modal');
  if (await modal.isVisible()) {
    await modal.evaluate(el => el.scrollTo(0, el.scrollHeight));
    await page.waitForTimeout(300);
    await page.screenshot({ path: `${SS}/deep2-08-modal-mobile-scrolled.png`, fullPage: true });

    // Check footer visibility
    const footerBox = await page.locator('.modal-footer').boundingBox().catch(() => null);
    console.log(`Modal footer box on mobile: ${JSON.stringify(footerBox)}`);
  }

  // Close
  await page.keyboard.press('Escape');
  await page.waitForTimeout(300);

  // ===== Detail view on mobile =====
  console.log('\n=== Detail view on mobile ===');
  await page.setViewportSize({ width: 375, height: 667 });
  const firstRow = page.locator('tbody tr').first();
  if (await firstRow.isVisible().catch(() => false)) {
    await firstRow.click();
    await page.waitForTimeout(2000);
    await page.screenshot({ path: `${SS}/deep2-09-detail-mobile.png`, fullPage: true });

    // Check stat card grid on mobile
    const statCards = await page.locator('.stat-card').count();
    console.log(`Stat cards on mobile: ${statCards}`);
    if (statCards > 0) {
      const firstCard = await page.locator('.stat-card').first().boundingBox();
      console.log(`First stat card dimensions: w=${firstCard?.width} h=${firstCard?.height}`);
    }

    // Check overview grid on mobile
    const overviewGrid = page.locator('[style*="grid-template-columns:1fr 1fr"]');
    if (await overviewGrid.isVisible().catch(() => false)) {
      const gridBox = await overviewGrid.boundingBox();
      console.log(`Overview 2-column grid on mobile: w=${gridBox?.width}`);
      // This grid doesn't have a responsive breakpoint - potential issue!
      if (gridBox && gridBox.width < 500) {
        console.log('  WARNING: 2-column overview grid on mobile - cards may be too narrow');
      }
    }

    await page.screenshot({ path: `${SS}/deep2-10-detail-mobile-scrolled.png`, fullPage: true });
  }

  // ===== Console messages summary =====
  console.log('\n========================================');
  console.log('=== CONSOLE MESSAGES SUMMARY ===');
  console.log('========================================');
  const errors = consoleMessages.filter(m => m.type === 'error' || m.type === 'pageerror');
  console.log(`Errors: ${errors.length}`);
  errors.forEach(e => console.log(`  [${e.type}] ${e.text}`));

  console.log(`\nAll messages: ${consoleMessages.length}`);
  consoleMessages.forEach(m => console.log(`  [${m.type}] ${m.text.substring(0, 150)}`));

  await browser.close();
  console.log('\nDeep inspection 2 complete.');
})();
