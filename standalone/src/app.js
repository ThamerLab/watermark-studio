(() => {
  "use strict";

  const MAX_FILE_BYTES = 100 * 1024 * 1024;
  const $ = (id) => document.getElementById(id);
  const { PDFDocument } = PDFLib;
  const state = {
    mode: "single",
    single: { source: null, settings: null, previewVersion: 0 },
    batch: { sources: [], rows: [], csvFile: null, settings: null, previewVersion: 0 },
  };

  function initializePdfWorker() {
    const raw = atob(window.__PDFJS_WORKER_BASE64__);
    const bytes = new Uint8Array(raw.length);
    for (let index = 0; index < raw.length; index += 1) bytes[index] = raw.charCodeAt(index);
    pdfjsLib.GlobalWorkerOptions.workerSrc = URL.createObjectURL(new Blob([bytes], { type: "text/javascript" }));
    delete window.__PDFJS_WORKER_BASE64__;
  }

  function humanSize(bytes) {
    if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} كيلوبايت`;
    return `${(bytes / 1024 / 1024).toFixed(1)} ميجابايت`;
  }

  function showError(id, message) {
    const element = $(id);
    element.textContent = message;
    element.classList.remove("hidden");
  }

  function clearError(id) {
    $(id).classList.add("hidden");
    $(id).textContent = "";
  }

  function safeFilename(value, fallback = "علامة") {
    let result = String(value || "").normalize("NFKC").replace(/[<>:"/\\|?*\u0000-\u001f]/g, "_");
    result = result.replace(/\s+/g, " ").replace(/[. ]+$/g, "").trim();
    if (!result) result = fallback;
    if (/^(con|prn|aux|nul|com[1-9]|lpt[1-9])(?:\..*)?$/i.test(result)) result = `_${result}`;
    return Array.from(result).slice(0, 80).join("");
  }

  function stem(filename) {
    return filename.replace(/\.pdf$/i, "");
  }

  async function validatePdfFile(file) {
    if (!(file instanceof File) || !/\.pdf$/i.test(file.name)) throw new Error("اختر ملفًا بامتداد PDF.");
    if (!file.size || file.size > MAX_FILE_BYTES) throw new Error("حجم ملف PDF يجب ألا يتجاوز 100 ميجابايت.");
    const signature = new Uint8Array(await file.slice(0, 5).arrayBuffer());
    if (String.fromCharCode(...signature) !== "%PDF-") throw new Error(`الملف «${file.name}» ليس ملف PDF صالحًا.`);
    const bytes = new Uint8Array(await file.arrayBuffer());
    let documentProxy;
    try {
      documentProxy = await pdfjsLib.getDocument({ data: bytes.slice() }).promise;
    } catch (_error) {
      throw new Error(`تعذر فتح «${file.name}». قد يكون تالفًا أو محميًا بكلمة مرور.`);
    }
    return { file, bytes, pdf: documentProxy, pageCount: documentProxy.numPages, baseCanvas: null };
  }

  function setupDropZone(zone, input) {
    for (const eventName of ["dragenter", "dragover"]) {
      zone.addEventListener(eventName, (event) => { event.preventDefault(); zone.classList.add("drag"); });
    }
    for (const eventName of ["dragleave", "drop"]) {
      zone.addEventListener(eventName, (event) => { event.preventDefault(); zone.classList.remove("drag"); });
    }
    zone.addEventListener("drop", (event) => {
      if (!event.dataTransfer.files.length) return;
      input.files = event.dataTransfer.files;
      input.dispatchEvent(new Event("change", { bubbles: true }));
    });
  }

  function mountSettings(containerId, mode) {
    const fragment = $("settingsTemplate").content.cloneNode(true);
    const container = $(containerId);
    container.append(fragment);
    if (mode === "batch") {
      container.querySelector(".text-field").remove();
      container.querySelector(".image-card").remove();
      const numbers = container.querySelectorAll(".section-number");
      if (numbers[0]) numbers[0].textContent = "03";
      if (numbers[1]) numbers[1].textContent = "04";
      container.querySelector('[data-field="opacity"]').value = "20";
      container.querySelector('[data-field="rotation"]').value = "-45";
      container.querySelector('[data-field="position"]').value = "tiled";
    }
    const context = { container, mode, imageFile: null, timer: null };
    container.querySelectorAll('[data-field="pages"]').forEach((input) => { input.name = `${mode}Pages`; });
    const update = () => {
      const fields = readSettings(context);
      for (const [name, suffix] of [["fontSize", ""], ["opacity", "%"], ["rotation", "°"], ["imageScale", "%"], ["imageOpacity", "%"]]) {
        const output = container.querySelector(`[data-output="${name}"]`);
        if (output && fields[name] !== undefined) output.textContent = `${fields[name]}${suffix}`;
      }
      const custom = container.querySelector("[data-custom-wrap]");
      if (custom) custom.classList.toggle("hidden", fields.pages !== "custom");
      clearTimeout(context.timer);
      context.timer = setTimeout(() => schedulePreview(mode), 80);
    };
    container.addEventListener("input", update);
    container.addEventListener("change", update);
    const imageInput = container.querySelector('[data-field="image"]');
    if (imageInput) {
      imageInput.addEventListener("change", () => {
        const file = imageInput.files[0];
        if (file && !["image/png", "image/jpeg"].includes(file.type)) {
          imageInput.value = "";
          context.imageFile = null;
          showError("singleError", "صورة العلامة يجب أن تكون PNG أو JPEG.");
          return;
        }
        context.imageFile = file || null;
        container.querySelector("[data-image-label]").textContent = file ? file.name : "اختر صورة العلامة";
        container.querySelector("[data-clear-image]").classList.toggle("hidden", !file);
        schedulePreview(mode);
      });
      container.querySelector("[data-clear-image]").addEventListener("click", () => {
        imageInput.value = "";
        context.imageFile = null;
        container.querySelector("[data-image-label]").textContent = "اختر صورة العلامة";
        container.querySelector("[data-clear-image]").classList.add("hidden");
        schedulePreview(mode);
      });
    }
    update();
    return context;
  }

  function readSettings(context, textOverride = null) {
    const field = (name) => context.container.querySelector(`[data-field="${name}"]`);
    return {
      text: textOverride ?? field("text")?.value ?? "",
      font: field("font").value,
      fontSize: Number(field("fontSize").value),
      opacity: Number(field("opacity").value) / 100,
      rotation: Number(field("rotation").value),
      color: field("color").value,
      position: field("position").value,
      pages: context.container.querySelector('[data-field="pages"]:checked').value,
      customPages: field("customPages").value,
      imageFile: context.imageFile,
      imageScale: Number(field("imageScale")?.value || 25) / 100,
      imageOpacity: Number(field("imageOpacity")?.value || 30) / 100,
      imagePosition: field("imagePosition")?.value || "center",
    };
  }

  function parsePageSelection(mode, customValue, pageCount) {
    if (mode === "all") return new Set(Array.from({ length: pageCount }, (_, index) => index));
    if (mode === "first") return new Set([0]);
    const selected = new Set();
    const value = String(customValue || "").trim();
    if (!value) throw new Error("اكتب أرقام الصفحات المطلوبة.");
    for (const token of value.split(",")) {
      const part = token.trim();
      if (/^\d+$/.test(part)) {
        const page = Number(part);
        if (page < 1 || page > pageCount) throw new Error(`رقم الصفحة ${page} خارج نطاق الملف.`);
        selected.add(page - 1);
      } else {
        const match = part.match(/^(\d+)\s*-\s*(\d+)$/);
        if (!match) throw new Error(`صيغة الصفحات غير صحيحة عند «${part}».`);
        const start = Number(match[1]);
        const end = Number(match[2]);
        if (start < 1 || end < start || end > pageCount) throw new Error(`نطاق الصفحات ${part} غير صالح.`);
        for (let page = start; page <= end; page += 1) selected.add(page - 1);
      }
    }
    if (!selected.size) throw new Error("لم يتم تحديد أي صفحة.");
    return selected;
  }

  function hexToRgba(hex) {
    const value = hex.replace("#", "");
    return {
      r: parseInt(value.slice(0, 2), 16),
      g: parseInt(value.slice(2, 4), 16),
      b: parseInt(value.slice(4, 6), 16),
    };
  }

  function makeTextStamp(text, settings, scale = 2) {
    const lines = String(text).replace(/\r/g, "").split("\n");
    const fontSize = settings.fontSize * scale;
    const measure = document.createElement("canvas").getContext("2d");
    measure.font = `${fontSize}px "${settings.font}", "Noto Sans Arabic Embedded", Tahoma, Arial, sans-serif`;
    const rawWidth = Math.max(1, ...lines.map((line) => measure.measureText(line || " ").width));
    const lineHeight = fontSize * 1.45;
    const padding = Math.max(12 * scale, fontSize * 0.35);
    const width = Math.ceil(rawWidth + padding * 2);
    const height = Math.ceil(lines.length * lineHeight + padding * 2);
    const angle = settings.rotation * Math.PI / 180;
    const rotatedWidth = Math.ceil(Math.abs(width * Math.cos(angle)) + Math.abs(height * Math.sin(angle)));
    const rotatedHeight = Math.ceil(Math.abs(width * Math.sin(angle)) + Math.abs(height * Math.cos(angle)));
    const canvas = document.createElement("canvas");
    canvas.width = Math.max(1, rotatedWidth);
    canvas.height = Math.max(1, rotatedHeight);
    const ctx = canvas.getContext("2d");
    ctx.translate(canvas.width / 2, canvas.height / 2);
    ctx.rotate(angle);
    ctx.font = measure.font;
    ctx.fillStyle = settings.color;
    ctx.textAlign = "center";
    ctx.textBaseline = "middle";
    ctx.direction = "rtl";
    lines.forEach((line, index) => ctx.fillText(line, 0, (index - (lines.length - 1) / 2) * lineHeight));
    return { canvas, width: canvas.width / scale, height: canvas.height / scale };
  }

  function placement(pageWidth, pageHeight, itemWidth, itemHeight, position) {
    const margin = 30;
    const centerX = (pageWidth - itemWidth) / 2;
    const centerY = (pageHeight - itemHeight) / 2;
    const values = {
      center: [centerX, centerY],
      "top-left": [margin, pageHeight - itemHeight - margin],
      "top-center": [centerX, pageHeight - itemHeight - margin],
      "top-right": [pageWidth - itemWidth - margin, pageHeight - itemHeight - margin],
      "bottom-left": [margin, margin],
      "bottom-center": [centerX, margin],
      "bottom-right": [pageWidth - itemWidth - margin, margin],
    };
    return values[position] || values.center;
  }

  function fitDimensions(width, height, maxWidth, maxHeight) {
    const factor = Math.min(1, maxWidth / width, maxHeight / height);
    return [width * factor, height * factor];
  }

  function drawPdfItem(page, embedded, width, height, position, opacity) {
    const { width: pageWidth, height: pageHeight } = page.getSize();
    [width, height] = fitDimensions(
      width,
      height,
      position === "tiled" ? pageWidth * 0.62 : pageWidth - 60,
      position === "tiled" ? pageHeight * 0.28 : pageHeight - 60,
    );
    if (position !== "tiled") {
      const [x, y] = placement(pageWidth, pageHeight, width, height, position);
      page.drawImage(embedded, { x, y, width, height, opacity });
      return;
    }
    const stepX = Math.max(width * 1.45, 190);
    const stepY = Math.max(height * 1.7, 135);
    let row = 0;
    for (let y = 25; y < pageHeight; y += stepY) {
      const offset = row % 2 ? -stepX / 2 : 0;
      for (let x = offset; x < pageWidth; x += stepX) page.drawImage(embedded, { x, y, width, height, opacity });
      row += 1;
    }
  }

  async function embedOptionalImage(pdfDocument, settings) {
    if (!settings.imageFile) return null;
    const bytes = new Uint8Array(await settings.imageFile.arrayBuffer());
    const embedded = settings.imageFile.type === "image/png" ? await pdfDocument.embedPng(bytes) : await pdfDocument.embedJpg(bytes);
    return embedded;
  }

  async function watermarkPdf(sourceBytes, settings, text) {
    let pdfDocument;
    try {
      pdfDocument = await PDFDocument.load(sourceBytes.slice(), { updateMetadata: false });
    } catch (_error) {
      throw new Error("تعذر تعديل PDF. الملفات المشفرة أو المحمية غير مدعومة.");
    }
    const pages = pdfDocument.getPages();
    const selected = parsePageSelection(settings.pages, settings.customPages, pages.length);
    let textImage = null;
    let textStamp = null;
    if (String(text).trim()) {
      textStamp = makeTextStamp(text, settings, 2);
      textImage = await pdfDocument.embedPng(textStamp.canvas.toDataURL("image/png"));
    }
    const optionalImage = await embedOptionalImage(pdfDocument, settings);
    pages.forEach((page, index) => {
      if (!selected.has(index)) return;
      if (textImage) drawPdfItem(page, textImage, textStamp.width, textStamp.height, settings.position, settings.opacity);
      if (optionalImage) {
        const ratio = optionalImage.height / optionalImage.width;
        const width = page.getWidth() * settings.imageScale;
        drawPdfItem(page, optionalImage, width, width * ratio, settings.imagePosition, settings.imageOpacity);
      }
    });
    return pdfDocument.save({ useObjectStreams: true });
  }

  async function sourceBaseCanvas(source) {
    if (source.baseCanvas) return source.baseCanvas;
    const page = await source.pdf.getPage(1);
    const original = page.getViewport({ scale: 1 });
    const scale = Math.min(1.6, 820 / original.width);
    const viewport = page.getViewport({ scale });
    const canvas = document.createElement("canvas");
    canvas.width = Math.ceil(viewport.width);
    canvas.height = Math.ceil(viewport.height);
    await page.render({ canvasContext: canvas.getContext("2d"), viewport }).promise;
    source.baseCanvas = { canvas, scale };
    return source.baseCanvas;
  }

  function canvasPlacement(canvasWidth, canvasHeight, itemWidth, itemHeight, position) {
    const margin = 30;
    const centerX = (canvasWidth - itemWidth) / 2;
    const centerY = (canvasHeight - itemHeight) / 2;
    const values = {
      center: [centerX, centerY],
      "top-left": [margin, margin], "top-center": [centerX, margin], "top-right": [canvasWidth - itemWidth - margin, margin],
      "bottom-left": [margin, canvasHeight - itemHeight - margin], "bottom-center": [centerX, canvasHeight - itemHeight - margin], "bottom-right": [canvasWidth - itemWidth - margin, canvasHeight - itemHeight - margin],
    };
    return values[position] || values.center;
  }

  function drawCanvasItem(ctx, drawable, width, height, position, opacity) {
    [width, height] = fitDimensions(
      width,
      height,
      position === "tiled" ? ctx.canvas.width * 0.62 : ctx.canvas.width - 60,
      position === "tiled" ? ctx.canvas.height * 0.28 : ctx.canvas.height - 60,
    );
    ctx.save();
    ctx.globalAlpha = opacity;
    if (position !== "tiled") {
      const [x, y] = canvasPlacement(ctx.canvas.width, ctx.canvas.height, width, height, position);
      ctx.drawImage(drawable, x, y, width, height);
    } else {
      const stepX = Math.max(width * 1.45, 190);
      const stepY = Math.max(height * 1.7, 135);
      let row = 0;
      for (let y = 25; y < ctx.canvas.height; y += stepY) {
        const offset = row % 2 ? -stepX / 2 : 0;
        for (let x = offset; x < ctx.canvas.width; x += stepX) ctx.drawImage(drawable, x, y, width, height);
        row += 1;
      }
    }
    ctx.restore();
  }

  async function drawPreview(canvasId, busyId, source, settings, text, versionCheck) {
    if (!source) return;
    $(busyId).classList.remove("hidden");
    await document.fonts.ready;
    const base = await sourceBaseCanvas(source);
    if (!versionCheck()) return;
    const canvas = $(canvasId);
    canvas.width = base.canvas.width;
    canvas.height = base.canvas.height;
    const ctx = canvas.getContext("2d");
    ctx.drawImage(base.canvas, 0, 0);
    if (String(text).trim()) {
      const stamp = makeTextStamp(text, settings, base.scale);
      drawCanvasItem(ctx, stamp.canvas, stamp.canvas.width, stamp.canvas.height, settings.position, settings.opacity);
    }
    if (settings.imageFile) {
      const bitmap = await createImageBitmap(settings.imageFile);
      const width = canvas.width * settings.imageScale;
      drawCanvasItem(ctx, bitmap, width, width * bitmap.height / bitmap.width, settings.imagePosition, settings.imageOpacity);
      bitmap.close();
    }
    if (versionCheck()) $(busyId).classList.add("hidden");
  }

  function schedulePreview(mode) {
    const target = state[mode];
    target.previewVersion += 1;
    const version = target.previewVersion;
    if (mode === "single" && target.source && target.settings) {
      const settings = readSettings(target.settings);
      drawPreview("singlePreview", "singlePreviewBusy", target.source, settings, settings.text, () => version === target.previewVersion)
        .catch((error) => showError("singleError", error.message));
    }
    if (mode === "batch" && target.sources.length && target.rows.length && target.settings) {
      const row = target.rows[0];
      const settings = readSettings(target.settings, row.watermark_text);
      drawPreview("batchPreview", "batchPreviewBusy", target.sources[0], settings, row.watermark_text, () => version === target.previewVersion)
        .catch((error) => showError("batchError", error.message));
    }
  }

  function downloadBlob(blob, filename) {
    const url = URL.createObjectURL(blob);
    const anchor = document.createElement("a");
    anchor.href = url;
    anchor.download = filename;
    document.body.append(anchor);
    anchor.click();
    anchor.remove();
    setTimeout(() => URL.revokeObjectURL(url), 30_000);
  }

  async function selectSingleFile(file) {
    clearError("singleError");
    clearError("singleUploadError");
    $("singleUploadCard").classList.add("hidden");
    try {
      state.single.source = await validatePdfFile(file);
      $("singleFileName").textContent = file.name;
      $("singleFileMeta").textContent = `${humanSize(file.size)} · ${state.single.source.pageCount} صفحة`;
      $("singlePageCount").textContent = `${state.single.source.pageCount} صفحة`;
      $("singleWorkspace").classList.remove("hidden");
      schedulePreview("single");
    } catch (error) {
      $("singleUploadCard").classList.remove("hidden");
      showError("singleUploadError", error.message);
      throw error;
    }
  }

  function resetSingle() {
    state.single.source?.pdf?.destroy();
    state.single.source = null;
    $("singleFile").value = "";
    $("singleWorkspace").classList.add("hidden");
    $("singleUploadCard").classList.remove("hidden");
    clearError("singleError");
    clearError("singleUploadError");
  }

  async function generateSingle() {
    clearError("singleError");
    const settings = readSettings(state.single.settings);
    if (!settings.text.trim() && !settings.imageFile) return showError("singleError", "أضف علامة نصية أو صورة أولًا.");
    $("singleGenerate").disabled = true;
    $("singleProgress").classList.remove("hidden");
    try {
      await new Promise((resolve) => setTimeout(resolve, 30));
      const bytes = await watermarkPdf(state.single.source.bytes, settings, settings.text);
      const name = `${safeFilename(stem(state.single.source.file.name), "document")} - علامة مائية.pdf`;
      downloadBlob(new Blob([bytes], { type: "application/pdf" }), name);
    } catch (error) {
      showError("singleError", error.message || "تعذر إنشاء ملف PDF.");
    } finally {
      $("singleGenerate").disabled = false;
      $("singleProgress").classList.add("hidden");
    }
  }

  function parseCsv(text) {
    const parsed = Papa.parse(text.replace(/^\uFEFF/, ""), { header: true, skipEmptyLines: "greedy", transformHeader: (header) => header.trim() });
    if (parsed.errors.length) throw new Error(`تعذر قراءة CSV: ${parsed.errors[0].message}`);
    if (!parsed.meta.fields.includes("watermark_text")) throw new Error("يجب أن يحتوي CSV على العمود المطلوب watermark_text.");
    const rows = parsed.data.map((row, index) => {
      const watermarkText = String(row.watermark_text ?? "").trim();
      if (!watermarkText) throw new Error(`نص العلامة مفقود في الصف ${index + 2}.`);
      const requested = String(row.output_name ?? "").trim();
      return {
        ...row,
        watermark_text: watermarkText,
        output_name: safeFilename(requested || watermarkText, `علامة-${index + 1}`),
      };
    });
    if (!rows.length) throw new Error("ملف CSV لا يحتوي على علامات مائية.");
    return rows;
  }

  function uniqueName(name, used) {
    const key = name.toLocaleLowerCase("en-US");
    if (!used.has(key)) { used.add(key); return name; }
    let index = 2;
    while (used.has(`${key} (${index})`)) index += 1;
    const result = `${Array.from(name).slice(0, 70).join("")} (${index})`;
    used.add(result.toLocaleLowerCase("en-US"));
    return result;
  }

  async function prepareBatch() {
    clearError("batchSetupError");
    $("batchPrepare").disabled = true;
    try {
      const files = Array.from($("batchFiles").files);
      const csvFile = $("batchCsv").files[0];
      if (!files.length || !csvFile) throw new Error("اختر ملفات PDF وملف CSV أولًا.");
      const rows = parseCsv(await csvFile.text());
      const sources = [];
      for (const file of files) sources.push(await validatePdfFile(file));
      state.batch.sources.forEach((source) => source.pdf?.destroy());
      state.batch.sources = sources;
      state.batch.rows = rows;
      $("batchImported").textContent = `تم استيراد ${rows.length} علامات مائية`;
      $("batchSources").textContent = `${sources.length} ملفات PDF`;
      $("batchEquation").textContent = `${sources.length} ملفات × ${rows.length} علامات مائية = ${sources.length * rows.length} ملف PDF`;
      $("batchPageCount").textContent = `${sources[0].pageCount} صفحة`;
      $("batchPreviewLabel").textContent = rows[0].output_name;
      $("batchRows").replaceChildren(...rows.map((row, index) => {
        const item = document.createElement("div");
        item.className = "batch-row";
        const content = document.createElement("div");
        const text = document.createElement("strong");
        text.textContent = row.watermark_text;
        const output = document.createElement("small");
        output.textContent = `المجلد: ${row.output_name}`;
        content.append(text, output);
        const number = document.createElement("span");
        number.textContent = `#${index + 1}`;
        item.append(content, number);
        return item;
      }));
      $("batchSetup").classList.add("hidden");
      $("batchWorkspace").classList.remove("hidden");
      schedulePreview("batch");
    } catch (error) {
      showError("batchSetupError", error.message);
    } finally {
      $("batchPrepare").disabled = !($("batchFiles").files.length && $("batchCsv").files.length);
    }
  }

  function csvCell(value) {
    return `"${String(value).replaceAll('"', '""')}"`;
  }

  async function generateBatch() {
    clearError("batchError");
    const baseSettings = readSettings(state.batch.settings);
    $("batchGenerate").disabled = true;
    $("batchProgress").classList.remove("hidden");
    try {
      const zip = new JSZip();
      const usedFolders = new Set();
      const manifest = [["watermark_text", "output_name", "source_file", "output_file"]];
      const total = state.batch.rows.length * state.batch.sources.length;
      let completed = 0;
      for (const row of state.batch.rows) {
        const folderName = uniqueName(row.output_name, usedFolders);
        const folder = zip.folder(folderName);
        const usedFiles = new Set();
        for (const source of state.batch.sources) {
          const settings = { ...baseSettings, text: row.watermark_text, imageFile: null };
          const bytes = await watermarkPdf(source.bytes, settings, row.watermark_text);
          const sourceName = safeFilename(stem(source.file.name), "document");
          const suffix = $("batchFilenameMode").value === "source-and-output" ? ` - ${folderName}` : "";
          const outputName = `${uniqueName(`${sourceName}${suffix}`, usedFiles)}.pdf`;
          folder.file(outputName, bytes);
          manifest.push([row.watermark_text, folderName, source.file.name, `${folderName}/${outputName}`]);
          completed += 1;
          const percent = Math.round(completed / total * 90);
          $("batchProgressBar").style.width = `${percent}%`;
          $("batchProgressText").textContent = `${percent}%`;
          $("batchProgressDetail").textContent = `${completed} من ${total} ملف PDF`;
          await new Promise((resolve) => setTimeout(resolve, 0));
        }
      }
      zip.file("_manifest.csv", `\uFEFF${manifest.map((row) => row.map(csvCell).join(",")).join("\r\n")}`);
      const blob = await zip.generateAsync({ type: "blob", compression: "DEFLATE", compressionOptions: { level: 6 } }, (metadata) => {
        const percent = 90 + Math.round(metadata.percent / 10);
        $("batchProgressBar").style.width = `${percent}%`;
        $("batchProgressText").textContent = `${percent}%`;
      });
      downloadBlob(blob, "watermark-studio-batch.zip");
    } catch (error) {
      showError("batchError", error.message || "تعذر إنشاء ملفات الدفعة.");
    } finally {
      $("batchGenerate").disabled = false;
      setTimeout(() => $("batchProgress").classList.add("hidden"), 500);
    }
  }

  function downloadCsvTemplate() {
    const template = '\uFEFFwatermark_text,output_name\r\n"نسخة خاصة - محمد أحمد","محمد أحمد"\r\n"للاستخدام الداخلي فقط","نسخة داخلية"\r\n';
    downloadBlob(new Blob([template], { type: "text/csv;charset=utf-8" }), "watermark-studio-template.csv");
  }

  function setMode(mode) {
    state.mode = mode;
    const single = mode === "single";
    $("singleMode").classList.toggle("hidden", !single);
    $("batchMode").classList.toggle("hidden", single);
    $("singleTab").classList.toggle("active", single);
    $("batchTab").classList.toggle("active", !single);
    $("singleTab").setAttribute("aria-selected", String(single));
    $("batchTab").setAttribute("aria-selected", String(!single));
    if (single) schedulePreview("single"); else schedulePreview("batch");
  }

  function updateBatchSelection() {
    const files = Array.from($("batchFiles").files);
    $("batchFileList").replaceChildren(...files.map((file) => {
      const item = document.createElement("li");
      item.textContent = `${file.name} — ${humanSize(file.size)}`;
      return item;
    }));
    $("batchPrepare").disabled = !(files.length && $("batchCsv").files.length);
  }

  async function boot() {
    initializePdfWorker();
    state.single.settings = mountSettings("singleSettings", "single");
    state.batch.settings = mountSettings("batchSettings", "batch");
    setupDropZone($("singleDrop"), $("singleFile"));
    setupDropZone($("batchDrop"), $("batchFiles"));
    $("singleTab").addEventListener("click", () => setMode("single"));
    $("batchTab").addEventListener("click", () => setMode("batch"));
    $("singleFile").addEventListener("change", () => {
      if ($("singleFile").files[0]) selectSingleFile($("singleFile").files[0]).catch(() => {});
    });
    $("singleReset").addEventListener("click", resetSingle);
    $("singleGenerate").addEventListener("click", generateSingle);
    $("batchFiles").addEventListener("change", updateBatchSelection);
    $("batchCsv").addEventListener("change", () => {
      const file = $("batchCsv").files[0];
      $("batchCsvLabel").textContent = file ? file.name : "اختر ملف CSV";
      updateBatchSelection();
    });
    $("batchPrepare").addEventListener("click", prepareBatch);
    $("batchGenerate").addEventListener("click", generateBatch);
    $("downloadTemplate").addEventListener("click", downloadCsvTemplate);
    window.__watermarkStudioReady = true;
  }

  window.__watermarkStudioTest = { parsePageSelection, safeFilename, parseCsv, fitDimensions };
  boot().catch((error) => {
    console.error(error);
    document.body.innerHTML = `<main><p class="error">تعذر تشغيل Watermark Studio: ${String(error.message || error)}</p></main>`;
  });
})();
