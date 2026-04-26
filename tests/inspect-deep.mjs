/**
 * Deep inspection: tooltip overflow, stat-card label clipping,
 * existing job with 100 URLs, filter interactions, and modal footer visibility.
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

  // ===== 1. Check tooltip overflow on stat-card =====
  console.log('\n=== CHECK 1: Tooltip overflow on first stat card ===');
  // Navigate to the job with 100 URLs (e-quironsalud)
  const rows = await page.locator('tbody tr').count();
  console.log(`Jobs in table: ${rows}`);

  // Click the "e-quironsalud SIN JS" row (last one with 100 crawled)
  const eqRow = page.locator('tbody tr:has-text("e-quironsalud")');
  if (await eqRow.isVisible().catch(() => false)) {
    await eqRow.click();
    await page.waitForTimeout(2000);
    await page.screenshot({ path: `${SS}/deep-01-equironsalud-overview.png`, fullPage: true });
    console.log('Screenshot: deep-01-equironsalud-overview.png');

    // Check stat cards text clipping
    const statCards = page.locator('.stat-card');
    const cardCount = await statCards.count();
    console.log(`Stat cards: ${cardCount}`);
    for (let i = 0; i < cardCount; i++) {
      const card = statCards.nth(i);
      const label = await card.locator('.stat-label').textContent();
      const value = await card.locator('.stat-value').textContent();
      const box = await card.boundingBox();
      console.log(`  Card ${i}: value="${value}" label="${label}" w=${box?.width} h=${box?.height}`);
    }

    // ===== 2. Check overview bar-charts with more data =====
    console.log('\n=== CHECK 2: Overview bar charts with 100 URLs ===');
    const barCharts = await page.locator('.bar-chart').count();
    console.log(`  Bar charts: ${barCharts}`);

    // Status code chart
    const statusBars = await page.locator('.card:has-text("Codigos de estado") .bar-row').count();
    console.log(`  Status code bars: ${statusBars}`);
    for (let i = 0; i < statusBars; i++) {
      const label = await page.locator('.card:has-text("Codigos de estado") .bar-label').nth(i).textContent();
      const count = await page.locator('.card:has-text("Codigos de estado") .bar-count').nth(i).textContent();
      console.log(`    ${label.trim()}: ${count.trim()}`);
    }

    // Issue chart
    const issueBars = await page.locator('.card:has-text("Principales problemas") .bar-row').count();
    console.log(`  Issue bars: ${issueBars}`);
    for (let i = 0; i < Math.min(issueBars, 5); i++) {
      const label = await page.locator('.card:has-text("Principales problemas") .bar-label').nth(i).textContent();
      const count = await page.locator('.card:has-text("Principales problemas") .bar-count').nth(i).textContent();
      console.log(`    ${label.trim()}: ${count.trim()}`);
    }

    // ===== 3. Check URLs tab with real data =====
    console.log('\n=== CHECK 3: URLs tab with 100 URLs ===');
    await page.locator('.tab:has-text("URLs")').click();
    await page.waitForTimeout(2000);
    await page.screenshot({ path: `${SS}/deep-02-equironsalud-urls.png`, fullPage: true });
    console.log('Screenshot: deep-02-equironsalud-urls.png');

    const urlRowCount = await page.locator('.table-wrap tbody tr').count().catch(() => 0);
    console.log(`  URL rows: ${urlRowCount}`);

    // Check for long URL truncation
    if (urlRowCount > 0) {
      const firstUrl = await page.locator('.table-wrap tbody tr:first-child td:first-child').textContent();
      console.log(`  First URL (truncated to 100 chars): "${firstUrl.trim().substring(0, 100)}"`);
    }

    // Check pagination
    const paginationText = await page.locator('.pagination').first().textContent().catch(() => 'N/A');
    console.log(`  Pagination: "${paginationText.trim()}"`);

    // Test filter interactions
    console.log('\n=== CHECK 4: URL filter interactions ===');
    const selects = page.locator('.flex.gap-1 select');
    const selectCount = await selects.count();
    console.log(`  Select dropdowns: ${selectCount}`);

    // Filter by 2xx
    if (selectCount >= 1) {
      await selects.first().selectOption('2xx');
      await page.waitForTimeout(1500);
      const filteredCount = await page.locator('.table-wrap tbody tr').count().catch(() => 0);
      console.log(`  After 2xx filter: ${filteredCount} rows`);
      await page.screenshot({ path: `${SS}/deep-03-urls-filtered-2xx.png`, fullPage: true });

      // Reset filter
      await selects.first().selectOption('');
      await page.waitForTimeout(1500);
    }

    // ===== 5. Check Problemas tab =====
    console.log('\n=== CHECK 5: Problemas tab ===');
    await page.locator('.tab:has-text("Problemas")').click();
    await page.waitForTimeout(2000);
    await page.screenshot({ path: `${SS}/deep-04-equironsalud-issues.png`, fullPage: true });
    console.log('Screenshot: deep-04-equironsalud-issues.png');

    const issueRows = await page.locator('.table-wrap tbody tr').count().catch(() => 0);
    console.log(`  Issue rows: ${issueRows}`);

    // Check severity colors
    if (issueRows > 0) {
      for (let i = 0; i < Math.min(issueRows, 5); i++) {
        const severity = await page.locator('.table-wrap tbody tr').nth(i).locator('td:first-child span').textContent();
        const type = await page.locator('.table-wrap tbody tr').nth(i).locator('td:nth-child(2)').textContent();
        console.log(`    Row ${i}: ${severity.trim()} - ${type.trim()}`);
      }
    }

    // ===== 6. Check Enlaces tab =====
    console.log('\n=== CHECK 6: Enlaces tab ===');
    await page.locator('.tab:has-text("Enlaces")').click();
    await page.waitForTimeout(2000);
    await page.screenshot({ path: `${SS}/deep-05-equironsalud-links.png`, fullPage: true });
    console.log('Screenshot: deep-05-equironsalud-links.png');

    const linkRows = await page.locator('.table-wrap tbody tr').count().catch(() => 0);
    const linksEmpty = await page.locator('.empty').isVisible().catch(() => false);
    console.log(`  Link rows: ${linkRows}`);
    console.log(`  Links empty state: ${linksEmpty}`);

    if (linkRows > 0) {
      // Check long anchor texts and URL truncation
      for (let i = 0; i < Math.min(linkRows, 3); i++) {
        const destUrl = await page.locator('.table-wrap tbody tr').nth(i).locator('td:nth-child(2) .truncate').textContent().catch(() => '?');
        const anchor = await page.locator('.table-wrap tbody tr').nth(i).locator('td:nth-child(3) .truncate').textContent().catch(() => '?');
        console.log(`    Link ${i}: to="${destUrl.trim().substring(0, 60)}" anchor="${anchor.trim().substring(0, 40)}"`);
      }
    }

    // Go back
    await page.locator('button:has-text("Trabajos")').click();
    await page.waitForTimeout(1000);
  }

  // ===== 7. Test modal scroll on small viewport =====
  console.log('\n=== CHECK 7: Modal on small viewport (768x600) ===');
  await page.setViewportSize({ width: 768, height: 600 });
  await page.waitForTimeout(500);
  await page.screenshot({ path: `${SS}/deep-06-responsive-768.png`, fullPage: true });
  console.log('Screenshot: deep-06-responsive-768.png');

  // Open modal
  await page.locator('button:has-text("Nuevo rastreo")').first().click();
  await page.waitForTimeout(500);
  await page.screenshot({ path: `${SS}/deep-07-modal-768.png`, fullPage: true });
  console.log('Screenshot: deep-07-modal-768.png');

  // Check if modal footer is visible (can scroll to it)
  const modalFooter = page.locator('.modal-footer');
  const footerVisible = await modalFooter.isVisible().catch(() => false);
  console.log(`  Modal footer visible without scroll: ${footerVisible}`);

  // Scroll modal to bottom
  await page.locator('.modal').evaluate(el => el.scrollTo(0, el.scrollHeight));
  await page.waitForTimeout(300);
  await page.screenshot({ path: `${SS}/deep-08-modal-768-scrolled.png`, fullPage: true });
  console.log('Screenshot: deep-08-modal-768-scrolled.png');

  const footerVisibleAfterScroll = await modalFooter.isVisible().catch(() => false);
  console.log(`  Modal footer visible after scroll: ${footerVisibleAfterScroll}`);

  // Close modal
  await page.locator('.modal-backdrop').click({ position: { x: 5, y: 5 } }).catch(() => {});
  await page.waitForTimeout(300);

  // ===== 8. Test at mobile 375px =====
  console.log('\n=== CHECK 8: Mobile viewport (375x667) ===');
  await page.setViewportSize({ width: 375, height: 667 });
  await page.waitForTimeout(500);
  await page.screenshot({ path: `${SS}/deep-09-mobile-375.png`, fullPage: true });
  console.log('Screenshot: deep-09-mobile-375.png');

  // Check for horizontal overflow
  const mobileOverflow = await page.evaluate(() =>
    document.documentElement.scrollWidth > document.documentElement.clientWidth
  );
  console.log(`  Has horizontal overflow: ${mobileOverflow}`);

  // Check if table is scrollable
  const tableWrap = page.locator('.table-wrap');
  if (await tableWrap.isVisible().catch(() => false)) {
    const tableBox = await tableWrap.boundingBox();
    console.log(`  Table wrapper width: ${tableBox?.width}`);
    const tableScrollWidth = await tableWrap.evaluate(el => el.scrollWidth);
    console.log(`  Table scroll width: ${tableScrollWidth}`);
    console.log(`  Table needs horizontal scroll: ${tableScrollWidth > (tableBox?.width || 0)}`);
  }

  // ===== 9. Check filter button interactions =====
  console.log('\n=== CHECK 9: Filter button interactions ===');
  await page.setViewportSize({ width: 1440, height: 900 });
  await page.waitForTimeout(500);

  // Click "Completado" filter
  const completadoBtn = page.locator('button:has-text("Completado")').first();
  if (await completadoBtn.isVisible().catch(() => false)) {
    await completadoBtn.click();
    await page.waitForTimeout(1500);
    await page.screenshot({ path: `${SS}/deep-10-filter-completado.png`, fullPage: true });
    console.log('Screenshot: deep-10-filter-completado.png');

    const filteredRows = await page.locator('tbody tr').count().catch(() => 0);
    console.log(`  Rows after "Completado" filter: ${filteredRows}`);

    // Check if "Limpiar" button appears
    const limpiarBtn = page.locator('button:has-text("Limpiar")');
    const limpiarVisible = await limpiarBtn.isVisible().catch(() => false);
    console.log(`  "Limpiar" button visible: ${limpiarVisible}`);

    // Click Limpiar to reset
    if (limpiarVisible) {
      await limpiarBtn.click();
      await page.waitForTimeout(1000);
    }
  }

  // ===== FINAL: Console errors throughout deep inspection =====
  console.log('\n========================================');
  console.log('=== DEEP INSPECTION CONSOLE MESSAGES ===');
  console.log('========================================');
  const errors = consoleMessages.filter(m => m.type === 'error' || m.type === 'pageerror');
  console.log(`Errors: ${errors.length}`);
  errors.forEach(e => console.log(`  [${e.type}] ${e.text}${e.stack ? '\n    ' + e.stack.split('\n')[0] : ''}`));

  const warnings = consoleMessages.filter(m => m.type === 'warning');
  console.log(`Warnings: ${warnings.length}`);
  warnings.forEach(w => console.log(`  [warning] ${w.text}`));

  console.log(`\nTotal console messages: ${consoleMessages.length}`);
  consoleMessages.forEach(m => console.log(`  [${m.type}] ${m.text.substring(0, 150)}`));

  await browser.close();
  console.log('\nDeep inspection complete.');
})();
