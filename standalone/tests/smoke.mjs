import assert from "node:assert/strict";
import { access, readFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join, resolve } from "node:path";
import { pathToFileURL } from "node:url";
import { chromium } from "playwright-core";
import { PDFDocument, StandardFonts } from "pdf-lib";
import JSZip from "jszip";

const root = resolve(import.meta.dirname, "../..");
const htmlPath = resolve(root, "watermark-studio.html");
const candidates = [
  "C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe",
  "C:/Program Files/Google/Chrome/Application/chrome.exe",
  "/usr/bin/google-chrome",
  "/usr/bin/chromium",
];
let executablePath;
for (const candidate of candidates) {
  try { await access(candidate); executablePath = candidate; break; } catch {}
}
if (!executablePath) throw new Error("No supported local Chromium browser found for standalone smoke test");

const source = await PDFDocument.create();
const font = await source.embedFont(StandardFonts.Helvetica);
for (let pageNumber = 1; pageNumber <= 2; pageNumber += 1) {
  const page = source.addPage([420, 595]);
  page.drawText(`Watermark Studio test page ${pageNumber}`, { x: 45, y: 530, size: 18, font });
}
const sourcePdf = Buffer.from(await source.save());

const browser = await chromium.launch({ executablePath, headless: true });
const context = await browser.newContext({ acceptDownloads: true });
const page = await context.newPage();
const errors = [];
const externalRequests = [];
page.on("pageerror", (error) => errors.push(error.message));
page.on("console", (message) => { if (message.type() === "error") errors.push(message.text()); });
page.on("request", (request) => {
  if (/^https?:/i.test(request.url())) externalRequests.push(request.url());
});

await page.goto(pathToFileURL(htmlPath).href);
await page.waitForFunction(() => window.__watermarkStudioReady === true);
assert.equal(await page.locator("html").getAttribute("dir"), "rtl");
assert.equal(externalRequests.length, 0, "standalone page must not make network requests");

const helpers = await page.evaluate(() => ({
  safe: window.__watermarkStudioTest.safeFilename('إدارة/المراجعة: المالية'),
  pages: [...window.__watermarkStudioTest.parsePageSelection("custom", "1-2", 2)],
  rows: window.__watermarkStudioTest.parseCsv('watermark_text,output_name\n"سري للغاية",""').length,
}));
assert.equal(helpers.safe, "إدارة_المراجعة_ المالية");
assert.deepEqual(helpers.pages, [0, 1]);
assert.equal(helpers.rows, 1);

await page.locator("#singleFile").setInputFiles({ name: "مزيف.pdf", mimeType: "application/pdf", buffer: Buffer.from("not a pdf") });
await page.locator("#singleUploadError").waitFor({ state: "visible" });
assert.match(await page.locator("#singleUploadError").textContent(), /ليس ملف PDF صالحًا/);

await page.locator("#singleFile").setInputFiles({ name: "وثيقة اختبار.pdf", mimeType: "application/pdf", buffer: sourcePdf });
await page.locator("#singleWorkspace").waitFor({ state: "visible" });
await page.waitForFunction(() => document.getElementById("singlePreview").width > 0);
await page.locator('#singleSettings [data-field="text"]').fill("نسخة خاصة - CONFIDENTIAL");
const singleDownload = page.waitForEvent("download");
await page.locator("#singleGenerate").click();
const generated = await singleDownload;
assert.match(generated.suggestedFilename(), /علامة مائية\.pdf$/);
const generatedPath = process.env.WATERMARK_QA_PDF || join(tmpdir(), `watermark-studio-${Date.now()}.pdf`);
await generated.saveAs(generatedPath);
const generatedBytes = await readFile(generatedPath);
const generatedPdf = await PDFDocument.load(generatedBytes);
assert.equal(generatedPdf.getPageCount(), 2);
assert.ok(generatedBytes.length > sourcePdf.length, "watermarked PDF should contain embedded watermark image");

await page.locator("#batchTab").click();
const templateDownload = page.waitForEvent("download");
await page.locator("#downloadTemplate").click();
const template = await templateDownload;
assert.equal(template.suggestedFilename(), "watermark-studio-template.csv");
const templatePath = join(tmpdir(), `watermark-studio-template-${Date.now()}.csv`);
await template.saveAs(templatePath);
const templateBytes = await readFile(templatePath);
assert.deepEqual([...templateBytes.subarray(0, 3)], [0xef, 0xbb, 0xbf]);
assert.match(templateBytes.toString("utf8"), /watermark_text,output_name/);
await page.locator("#batchFiles").setInputFiles([
  { name: "دليل الدورة.pdf", mimeType: "application/pdf", buffer: sourcePdf },
  { name: "Module 1.pdf", mimeType: "application/pdf", buffer: sourcePdf },
]);
await page.locator("#batchCsv").setInputFiles({
  name: "watermarks.csv",
  mimeType: "text/csv",
  buffer: Buffer.from('\uFEFFwatermark_text,output_name\r\n"نسخة خاصة - محمد أحمد","محمد أحمد"\r\n"للاستخدام الداخلي فقط","نسخة داخلية"', "utf8"),
});
await page.locator("#batchPrepare").click();
await page.locator("#batchWorkspace").waitFor({ state: "visible" });
await page.waitForFunction(() => document.getElementById("batchPreview").width > 0);
assert.equal(await page.locator("#batchImported").textContent(), "تم استيراد 2 علامات مائية");
assert.equal(await page.locator("#batchEquation").textContent(), "2 ملفات × 2 علامات مائية = 4 ملف PDF");

const batchDownload = page.waitForEvent("download");
await page.locator("#batchGenerate").click();
const zipped = await batchDownload;
assert.equal(zipped.suggestedFilename(), "watermark-studio-batch.zip");
const zipPath = join(tmpdir(), `watermark-studio-${Date.now()}.zip`);
await zipped.saveAs(zipPath);
const archive = await JSZip.loadAsync(await readFile(zipPath));
const names = Object.keys(archive.files);
assert.ok(names.includes("_manifest.csv"));
assert.ok(names.some((name) => name.startsWith("محمد أحمد/") && name.endsWith(".pdf")));
assert.ok(names.some((name) => name.startsWith("نسخة داخلية/") && name.endsWith(".pdf")));
assert.equal(names.filter((name) => name.endsWith(".pdf")).length, 4);
const manifest = await archive.file("_manifest.csv").async("nodebuffer");
assert.deepEqual([...manifest.subarray(0, 3)], [0xef, 0xbb, 0xbf]);

assert.deepEqual(errors, [], `browser errors: ${errors.join(" | ")}`);
assert.equal(externalRequests.length, 0, "standalone workflow must stay offline");
await browser.close();
console.log("Standalone smoke test passed: offline load, Arabic PDF, page selection, batch ZIP, BOM CSV");
