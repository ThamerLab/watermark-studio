const $ = id => document.getElementById(id);
let job = null;
let imageFile = null;
let imageUrl = null;
let batchJob = null;
let batchFiles = [];
let batchCsvFile = null;
let selectedWatermark = 0;
let selectedSource = 0;
let selectedPage = 1;
let reviewedWatermarks = new Set();
let preflightData = null;
let exactPreviewUrl = null;
let rapidPreviewUrls = [];
let preflightTimer = null;
let batchProcessing = false;

function showError(id, message) {
  $(id).textContent = message;
  $(id).classList.remove('hidden');
}
function clearError(id) { $(id).classList.add('hidden'); }
function formatBytes(bytes) {
  return bytes < 1024 * 1024 ? `${(bytes / 1024).toFixed(1)} KB` : `${(bytes / 1024 / 1024).toFixed(1)} MB`;
}
async function api(url, options = {}) {
  const response = await fetch(url, options);
  if (!response.ok) {
    let message = 'حدث خطأ غير متوقع.';
    try { message = (await response.json()).detail || message; } catch {}
    throw new Error(message);
  }
  return response.status === 204 ? null : response.json();
}

function setMode(mode) {
  const single = mode === 'single';
  $('singleMode').classList.toggle('hidden', !single);
  $('batchMode').classList.toggle('hidden', single);
  $('singleModeButton').classList.toggle('active', single);
  $('batchModeButton').classList.toggle('active', !single);
  $('singleModeButton').setAttribute('aria-selected', String(single));
  $('batchModeButton').setAttribute('aria-selected', String(!single));
}
$('singleModeButton').addEventListener('click', () => setMode('single'));
$('batchModeButton').addEventListener('click', () => setMode('batch'));

const positions = {
  center: ['50%', '50%'], 'top-left': ['18%', '14%'], 'top-center': ['50%', '14%'],
  'top-right': ['82%', '14%'], 'bottom-left': ['18%', '86%'], 'bottom-center': ['50%', '86%'], 'bottom-right': ['82%', '86%']
};
const fontFamilies = {
  'noto-sans': '"Noto Sans Arabic", Arial, sans-serif', 'noto-naskh': '"Noto Naskh Arabic", serif',
  'noto-kufi': '"Noto Kufi Arabic", Arial, sans-serif', carlito: 'Carlito, Arial, sans-serif'
};
function xmlEscape(value) {
  return value.replace(/[&<>"']/g, character => ({'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&apos;'}[character]));
}
function updateTextPreview(canvas, overlay, settings) {
  overlay.textContent = settings.text;
  overlay.style.fontSize = `${Math.max(10, settings.fontSize / 2.8)}px`;
  overlay.style.fontFamily = fontFamilies[settings.fontName] || fontFamilies['noto-sans'];
  overlay.style.color = settings.color;
  overlay.style.opacity = settings.opacity / 100;
  canvas.classList.toggle('tiled', settings.position === 'tiled');
  if (settings.position === 'tiled') {
    const svg = `<svg xmlns="http://www.w3.org/2000/svg" width="220" height="140"><text x="110" y="70" text-anchor="middle" fill="${settings.color}" font-size="16" font-family="Arial" transform="rotate(${settings.rotation} 110 70)">${xmlEscape(settings.text)}</text></svg>`;
    overlay.style.backgroundImage = `url("data:image/svg+xml,${encodeURIComponent(svg)}")`;
    overlay.style.inset = '0';
    overlay.style.transform = 'none';
  } else {
    overlay.style.backgroundImage = 'none';
    overlay.style.inset = 'auto';
    const [left, top] = positions[settings.position] || positions.center;
    overlay.style.left = left;
    overlay.style.top = top;
    overlay.style.transform = `translate(-50%,-50%) rotate(${settings.rotation}deg)`;
  }
}

// Single-file workflow
const dropZone = $('dropZone');
const documentInput = $('documentInput');
async function uploadSingle(file) {
  clearError('errorBox');
  if (!/\.(pdf|pptx)$/i.test(file.name)) { showError('errorBox', 'اختر ملف PDF أو PPTX.'); return; }
  $('uploadProgress').classList.remove('hidden');
  dropZone.classList.add('hidden');
  const form = new FormData();
  form.append('file', file);
  try {
    job = await api('/api/jobs', {method: 'POST', body: form});
    $('uploadCard').classList.add('hidden');
    $('workspace').classList.remove('hidden');
    $('fileName').textContent = job.filename;
    $('fileMeta').textContent = `${job.extension.slice(1).toUpperCase()} · ${formatBytes(job.size)} · ${job.page_count} صفحة`;
    $('pageCount').textContent = `${job.page_count} صفحة`;
    $('pagePreview').src = `${job.preview_url}?v=${Date.now()}`;
    updateSinglePreview();
  } catch (error) {
    showError('errorBox', error.message);
    dropZone.classList.remove('hidden');
  } finally { $('uploadProgress').classList.add('hidden'); }
}
documentInput.addEventListener('change', () => documentInput.files[0] && uploadSingle(documentInput.files[0]));
['dragenter', 'dragover'].forEach(event => dropZone.addEventListener(event, e => { e.preventDefault(); dropZone.classList.add('drag'); }));
['dragleave', 'drop'].forEach(event => dropZone.addEventListener(event, e => { e.preventDefault(); dropZone.classList.remove('drag'); }));
dropZone.addEventListener('drop', e => e.dataTransfer.files[0] && uploadSingle(e.dataTransfer.files[0]));

function singleSettings() {
  return {text: $('watermarkText').value, fontName: $('fontName').value, fontSize: Number($('fontSize').value), opacity: Number($('opacity').value), rotation: Number($('rotation').value), color: $('color').value, position: $('position').value};
}
function updateSinglePreview() {
  updateTextPreview($('previewCanvas'), $('textOverlay'), singleSettings());
  if (imageUrl) {
    const image = $('imageOverlay');
    const [left, top] = positions[$('imagePosition').value] || positions.center;
    image.src = imageUrl;
    image.classList.remove('hidden');
    image.style.left = left; image.style.top = top; image.style.width = `${$('imageScale').value}%`;
    image.style.opacity = $('imageOpacity').value / 100; image.style.transform = 'translate(-50%,-50%)';
  } else { $('imageOverlay').classList.add('hidden'); }
}
[
  ['fontSize', 'fontSizeOut', value => value], ['opacity', 'opacityOut', value => `${value}%`],
  ['rotation', 'rotationOut', value => `${value}°`], ['imageScale', 'imageScaleOut', value => `${value}%`],
  ['imageOpacity', 'imageOpacityOut', value => `${value}%`]
].forEach(([id, output, format]) => $(id).addEventListener('input', () => { $(output).value = format($(id).value); updateSinglePreview(); }));
['watermarkText', 'fontName', 'color', 'position', 'imagePosition'].forEach(id => $(id).addEventListener('input', updateSinglePreview));
$('imageInput').addEventListener('change', () => {
  imageFile = $('imageInput').files[0] || null;
  if (imageUrl) URL.revokeObjectURL(imageUrl);
  imageUrl = imageFile ? URL.createObjectURL(imageFile) : null;
  $('imageLabel').textContent = imageFile ? imageFile.name : 'اختر صورة العلامة';
  updateSinglePreview();
});
document.querySelectorAll('input[name=pages]').forEach(input => input.addEventListener('change', () => $('customPagesWrap').classList.toggle('hidden', input.value !== 'custom' || !input.checked)));
$('generateButton').addEventListener('click', async () => {
  clearError('errorBox'); $('resultBox').classList.add('hidden');
  const button = $('generateButton'); button.disabled = true; button.querySelector('span').textContent = 'جارٍ إنشاء الملف…';
  const settings = singleSettings(); const form = new FormData();
  form.append('watermark_text', settings.text); form.append('font_name', settings.fontName); form.append('font_size', settings.fontSize);
  form.append('opacity', settings.opacity / 100); form.append('rotation', settings.rotation); form.append('color', settings.color); form.append('position', settings.position);
  form.append('page_mode', document.querySelector('input[name=pages]:checked').value); form.append('custom_pages', $('customPages').value);
  form.append('image_position', $('imagePosition').value); form.append('image_scale', $('imageScale').value); form.append('image_opacity', $('imageOpacity').value / 100);
  if (imageFile) form.append('image_watermark', imageFile);
  try { const result = await api(`/api/jobs/${job.id}/generate`, {method: 'POST', body: form}); $('downloadLink').href = result.download_url; $('resultBox').classList.remove('hidden'); }
  catch (error) { showError('errorBox', error.message); }
  finally { button.disabled = false; button.querySelector('span').textContent = 'إنشاء PDF'; }
});
async function deleteJob(current) {
  if (!current) return;
  try {
    await api(`/api/jobs/${current.id}`, {method: 'DELETE'});
    if (localStorage.getItem('watermarkBatchJobId') === current.id) localStorage.removeItem('watermarkBatchJobId');
    location.reload();
  } catch (error) {
    const errorId = current === batchJob ? 'batchErrorBox' : 'errorBox';
    showError(errorId, error.message);
  }
}
$('deleteButton').addEventListener('click', () => deleteJob(job));
$('replaceFile').addEventListener('click', () => deleteJob(job));

// Generic batch workflow
function updateBatchReady() {
  $('prepareBatchButton').disabled = batchFiles.length === 0 || !batchCsvFile;
  $('batchFilesList').replaceChildren(...batchFiles.map(file => {
    const item = document.createElement('li'); item.textContent = `${file.name} · ${formatBytes(file.size)}`; return item;
  }));
  $('batchCsvLabel').textContent = batchCsvFile ? batchCsvFile.name : 'اختر ملف CSV';
}
function setBatchFiles(files) {
  batchFiles = Array.from(files).filter(file => /\.(pdf|pptx)$/i.test(file.name));
  if (batchFiles.length !== files.length) showError('batchSetupError', 'تم تجاهل ملفات غير مدعومة؛ استخدم PDF أو PPTX فقط.');
  updateBatchReady();
}
function setBatchCsv(files) {
  const file = files[0];
  if (file && !/\.csv$/i.test(file.name)) { showError('batchSetupError', 'اختر ملف CSV.'); return; }
  batchCsvFile = file || null; updateBatchReady();
}
$('batchFilesInput').addEventListener('change', () => setBatchFiles($('batchFilesInput').files));
$('batchCsvInput').addEventListener('change', () => setBatchCsv($('batchCsvInput').files));
function enableDrop(zone, onFiles) {
  ['dragenter', 'dragover'].forEach(event => zone.addEventListener(event, e => { e.preventDefault(); zone.classList.add('drag'); }));
  ['dragleave', 'drop'].forEach(event => zone.addEventListener(event, e => { e.preventDefault(); zone.classList.remove('drag'); }));
  zone.addEventListener('drop', e => onFiles(e.dataTransfer.files));
}
enableDrop($('batchFilesZone'), setBatchFiles); enableDrop($('batchCsvZone'), setBatchCsv);
$('prepareBatchButton').addEventListener('click', async () => {
  clearError('batchSetupError'); $('prepareBatchButton').disabled = true; $('batchProgress').classList.remove('hidden');
  const form = new FormData(); batchFiles.forEach(file => form.append('files', file)); form.append('watermarks_csv', batchCsvFile);
  try {
    batchJob = await api('/api/batch/jobs', {method: 'POST', body: form});
    localStorage.setItem('watermarkBatchJobId', batchJob.id);
    displayBatchJob();
  } catch (error) { showError('batchSetupError', error.message); $('prepareBatchButton').disabled = false; }
  finally { $('batchProgress').classList.add('hidden'); }
});
function displayBatchJob() {
  $('batchSetup').classList.add('hidden'); $('batchWorkspace').classList.remove('hidden');
  $('batchImported').textContent = `تم استيراد ${batchJob.watermark_count} علامات مائية`;
  $('batchSourcesSummary').textContent = `${batchJob.source_count} ملفات مصدر · ${formatBytes(batchJob.total_size)}`;
  $('batchEquation').textContent = `${batchJob.source_count} ملفات × ${batchJob.watermark_count} علامات مائية = ${batchJob.output_count} ملف PDF`;
  reviewedWatermarks = new Set((batchJob.review && batchJob.review.reviewed) || []);
  selectedWatermark = Math.min(selectedWatermark, batchJob.watermarks.length - 1);
  selectedSource = Math.min(selectedSource, batchJob.sources.length - 1);
  const watermarkOptions = batchJob.watermarks.map((row, index) => new Option(`${index + 1}. ${row.output_name}`, index));
  $('exactWatermark').replaceChildren(...watermarkOptions);
  const sourceOptions = batchJob.sources.map((source, index) => new Option(source.filename, index));
  $('exactSource').replaceChildren(...sourceOptions);
  $('rapidSource').replaceChildren(...batchJob.sources.map((source, index) => new Option(source.filename, index)));
  $('exactWatermark').value = String(selectedWatermark); $('exactSource').value = String(selectedSource); $('rapidSource').value = String(selectedSource);
  $('batchSampleText').textContent = batchJob.watermarks[selectedWatermark].watermark_text;
  $('batchPageCount').textContent = `${batchJob.sources[selectedSource].page_count} صفحة`;
  $('batchPagePreview').src = `${batchJob.preview_url}?v=${Date.now()}`;
  updateExactPage(); updateBatchPreview(); renderReviewList(); updateReviewSummary();
  runPreflight().then(() => requestExactPreview()).catch(error => showError('batchErrorBox', error.message));
}
function batchSettings() {
  return {text: batchJob ? batchJob.watermarks[selectedWatermark].watermark_text : '', fontName: $('batchFontName').value, fontSize: Number($('batchFontSize').value), opacity: Number($('batchOpacity').value), rotation: Number($('batchRotation').value), color: $('batchColor').value, position: $('batchPosition').value};
}
function updateBatchPreview() { updateTextPreview($('batchPreviewCanvas'), $('batchTextOverlay'), batchSettings()); }
function appendRenderingSettings(form) {
  const settings = batchSettings();
  form.append('font_name', settings.fontName); form.append('font_size', settings.fontSize); form.append('opacity', settings.opacity / 100);
  form.append('rotation', settings.rotation); form.append('color', settings.color); form.append('position', settings.position);
}
function schedulePreflight() {
  clearTimeout(preflightTimer);
  preflightTimer = setTimeout(() => runPreflight().catch(error => showError('batchErrorBox', error.message)), 450);
}
[
  ['batchFontSize', 'batchFontSizeOut', value => value], ['batchOpacity', 'batchOpacityOut', value => `${value}%`],
  ['batchRotation', 'batchRotationOut', value => `${value}°`]
].forEach(([id, output, format]) => $(id).addEventListener('input', () => { $(output).value = format($(id).value); updateBatchPreview(); schedulePreflight(); }));
['batchFontName', 'batchColor', 'batchPosition'].forEach(id => $(id).addEventListener('input', () => { updateBatchPreview(); schedulePreflight(); }));
$('batchFilenameMode').addEventListener('change', schedulePreflight);
document.querySelectorAll('input[name=batchPages]').forEach(input => input.addEventListener('change', () => $('batchCustomPagesWrap').classList.toggle('hidden', input.value !== 'custom' || !input.checked)));

function updateReviewSummary() {
  $('reviewCount').textContent = `${reviewedWatermarks.size} من ${batchJob ? batchJob.watermark_count : 0} تمت مراجعتها`;
  const currentReviewed = reviewedWatermarks.has(selectedWatermark);
  $('approveWatermarkButton').textContent = currentReviewed ? '✓ تم اعتماد هذه العلامة' : '✓ اعتماد هذه العلامة';
  $('approveWatermarkButton').classList.toggle('reviewed', currentReviewed);
  const strictBlocked = $('batchStrictReview').checked && batchJob && reviewedWatermarks.size !== batchJob.watermark_count;
  $('generateBatchButton').disabled = batchProcessing || strictBlocked;
}
function itemPreflight(index) { return preflightData ? preflightData.items.find(item => item.index === index) : null; }
function selectWatermark(index, exact = false) {
  selectedWatermark = Number(index);
  $('exactWatermark').value = String(selectedWatermark);
  $('batchSampleText').textContent = batchJob.watermarks[selectedWatermark].watermark_text;
  updateBatchPreview(); renderReviewList(); updateReviewSummary();
  if (exact) requestExactPreview().catch(error => showError('batchErrorBox', error.message));
}
function renderReviewList() {
  if (!batchJob) return;
  const query = $('reviewSearch').value.trim().toLocaleLowerCase();
  const filter = $('reviewFilter').value;
  const items = [];
  batchJob.watermarks.forEach((row, index) => {
    const check = itemPreflight(index);
    const warning = check && check.state !== 'ok';
    const isReviewed = reviewedWatermarks.has(index);
    const matchesQuery = !query || `${row.watermark_text} ${row.output_name}`.toLocaleLowerCase().includes(query);
    const matchesFilter = filter === 'all' || (filter === 'reviewed' && isReviewed) || (filter === 'pending' && !isReviewed) || (filter === 'warning' && warning);
    if (!matchesQuery || !matchesFilter) return;
    const button = document.createElement('button'); button.type = 'button';
    button.className = `review-item${index === selectedWatermark ? ' active' : ''}${warning ? ' warning' : ''}`;
    const state = document.createElement('span'); state.className = 'state'; state.textContent = check && check.state === 'error' ? '✕' : (warning ? '⚠' : (isReviewed ? '✓' : '○'));
    const title = document.createElement('strong'); title.textContent = row.output_name;
    const text = document.createElement('small'); text.textContent = row.watermark_text;
    button.append(state, title, text); button.addEventListener('click', () => selectWatermark(index, true)); items.push(button);
  });
  $('reviewList').replaceChildren(...items);
}
function renderPreflight() {
  if (!preflightData) return;
  const summary = preflightData.summary;
  $('preflightSummary').textContent = `✓ ${summary.ok} سليمة · ⚠ ${summary.warning} تحذير · ✕ ${summary.error} خطأ`;
  const nodes = preflightData.items.map(item => {
    const button = document.createElement('button'); button.type = 'button'; button.className = `preflight-item ${item.state}`;
    const title = document.createElement('strong'); title.textContent = `${item.state === 'ok' ? '✓' : item.state === 'warning' ? '⚠' : '✕'} ${item.output_name}`;
    const message = document.createElement('small'); message.textContent = item.messages.join(' ');
    button.append(title, message); button.addEventListener('click', () => selectWatermark(item.index, true)); return button;
  });
  $('preflightResults').replaceChildren(...nodes); renderReviewList();
}
async function runPreflight() {
  if (!batchJob) return null;
  const form = new FormData(); appendRenderingSettings(form); form.append('filename_mode', $('batchFilenameMode').value);
  preflightData = await api(`/api/batch/jobs/${batchJob.id}/preflight`, {method: 'POST', body: form});
  reviewedWatermarks = new Set(preflightData.review.reviewed); renderPreflight(); updateReviewSummary(); return preflightData;
}
function updateExactPage() {
  if (!batchJob) return;
  const total = batchJob.sources[selectedSource].page_count;
  selectedPage = Math.max(1, Math.min(total, selectedPage));
  $('exactPageNumber').value = selectedPage; $('exactPageNumber').max = total; $('exactPageLabel').textContent = `/ ${total}`;
  $('exactPrev').disabled = selectedPage <= 1; $('exactNext').disabled = selectedPage >= total;
  $('rapidPage').max = batchJob.sources[Number($('rapidSource').value || 0)].page_count;
}
async function exactPreviewBlob(watermarkIndex, sourceIndex, pageNumber) {
  const form = new FormData(); appendRenderingSettings(form);
  form.append('watermark_index', watermarkIndex); form.append('source_index', sourceIndex); form.append('page_number', pageNumber);
  const response = await fetch(`/api/batch/jobs/${batchJob.id}/preview`, {method: 'POST', body: form});
  if (!response.ok) { let message = 'تعذر إنشاء المعاينة الدقيقة.'; try { message = (await response.json()).detail || message; } catch {} throw new Error(message); }
  return URL.createObjectURL(await response.blob());
}
async function requestExactPreview() {
  clearError('batchErrorBox'); const button = $('exactPreviewButton'); button.disabled = true; button.textContent = 'جارٍ إنشاء المعاينة…';
  try {
    const url = await exactPreviewBlob(selectedWatermark, selectedSource, selectedPage);
    if (exactPreviewUrl) URL.revokeObjectURL(exactPreviewUrl); exactPreviewUrl = url;
    $('exactPreviewImage').src = url; $('exactPreviewEmpty').classList.add('hidden');
  } finally { button.disabled = false; button.textContent = 'تحديث المعاينة الدقيقة'; }
}
$('exactWatermark').addEventListener('change', event => selectWatermark(event.target.value, true));
$('exactSource').addEventListener('change', event => { selectedSource = Number(event.target.value); selectedPage = 1; updateExactPage(); requestExactPreview().catch(error => showError('batchErrorBox', error.message)); });
$('exactPrev').addEventListener('click', () => { selectedPage -= 1; updateExactPage(); requestExactPreview().catch(error => showError('batchErrorBox', error.message)); });
$('exactNext').addEventListener('click', () => { selectedPage += 1; updateExactPage(); requestExactPreview().catch(error => showError('batchErrorBox', error.message)); });
$('exactPageNumber').addEventListener('change', event => { selectedPage = Number(event.target.value); updateExactPage(); requestExactPreview().catch(error => showError('batchErrorBox', error.message)); });
$('exactPreviewButton').addEventListener('click', () => requestExactPreview().catch(error => showError('batchErrorBox', error.message)));
$('approveWatermarkButton').addEventListener('click', async () => {
  const form = new FormData(); form.append('reviewed', reviewedWatermarks.has(selectedWatermark) ? 'false' : 'true');
  const state = await api(`/api/batch/jobs/${batchJob.id}/review/${selectedWatermark}`, {method: 'POST', body: form});
  reviewedWatermarks = new Set(state.reviewed); renderReviewList(); updateReviewSummary();
});
$('reviewSearch').addEventListener('input', renderReviewList); $('reviewFilter').addEventListener('change', renderReviewList); $('batchStrictReview').addEventListener('change', updateReviewSummary);
$('rapidSource').addEventListener('change', () => { $('rapidPage').value = 1; updateExactPage(); });
$('rapidReviewButton').addEventListener('click', async () => {
  rapidPreviewUrls.forEach(URL.revokeObjectURL); rapidPreviewUrls = []; const sequence = Date.now(); $('rapidGrid').dataset.sequence = sequence;
  const sourceIndex = Number($('rapidSource').value); const page = Math.max(1, Math.min(Number($('rapidPage').value), batchJob.sources[sourceIndex].page_count));
  const cards = batchJob.watermarks.map((row, index) => {
    const card = document.createElement('button'); card.type = 'button'; card.className = 'rapid-item'; card.dataset.index = index;
    const title = document.createElement('strong'); title.textContent = row.output_name; const loading = document.createElement('span'); loading.textContent = 'جارٍ التحميل…'; card.append(title, loading); return card;
  });
  $('rapidGrid').replaceChildren(...cards); const queue = cards.map((_, index) => index);
  const worker = async () => { while (queue.length && $('rapidGrid').dataset.sequence === String(sequence)) { const index = queue.shift(); try { const url = await exactPreviewBlob(index, sourceIndex, page); rapidPreviewUrls.push(url); const image = document.createElement('img'); image.src = url; image.alt = batchJob.watermarks[index].output_name; cards[index].replaceChild(image, cards[index].lastChild); cards[index].addEventListener('click', () => selectWatermark(index, true)); } catch { cards[index].lastChild.textContent = 'تعذرت المعاينة'; } } };
  await Promise.all(Array.from({length: Math.min(6, queue.length)}, worker));
});

let batchPollSequence = 0;
const sleep = milliseconds => new Promise(resolve => setTimeout(resolve, milliseconds));
function showBatchStatus(status) {
  $('batchGenerationProgress').classList.remove('hidden');
  $('batchProgressBar').style.width = `${Math.max(0, Math.min(100, status.percent || 0))}%`;
  $('batchProgressPercent').textContent = `${status.percent || 0}%`;
  $('batchProgressMessage').textContent = status.message || 'جارٍ معالجة الدفعة';
  $('batchProgressCount').textContent = `${status.current || 0} من ${status.total || batchJob.output_count} ملف PDF`;
}
function finishBatch(status) {
  batchProcessing = false;
  showBatchStatus(status);
  $('batchGenerationProgress').classList.add('hidden');
  $('batchResultText').textContent = `تم إنشاء ${status.generated_count} ملف PDF بنجاح`;
  $('batchDownloadLink').href = status.download_url;
  $('batchResultBox').classList.remove('hidden');
  updateReviewSummary();
  $('generateBatchButton').querySelector('span').textContent = 'إنشاء ملفات الدفعة';
}
async function pollBatchStatus(statusUrl) {
  const sequence = ++batchPollSequence;
  while (sequence === batchPollSequence) {
    try {
      const status = await api(statusUrl);
      showBatchStatus(status);
      if (status.state === 'ready') { finishBatch(status); return; }
      if (status.state === 'failed') {
        batchProcessing = false;
        showError('batchErrorBox', status.error || 'فشلت معالجة الدفعة.');
        updateReviewSummary();
        $('generateBatchButton').querySelector('span').textContent = 'إعادة إنشاء ملفات الدفعة';
        return;
      }
    } catch (error) {
      batchProcessing = false;
      showError('batchErrorBox', error.message);
      updateReviewSummary();
      return;
    }
    await sleep(1000);
  }
}
$('generateBatchButton').addEventListener('click', async () => {
  clearError('batchErrorBox'); $('batchResultBox').classList.add('hidden');
  const button = $('generateBatchButton');
  try {
    const checks = await runPreflight();
    if (checks.summary.error) { showError('batchErrorBox', 'عالج أخطاء فحص ما قبل التصدير أولًا.'); return; }
    if ($('batchStrictReview').checked && reviewedWatermarks.size !== batchJob.watermark_count) { showError('batchErrorBox', 'اعتمد جميع العلامات قبل التصدير.'); return; }
    if (!$('batchStrictReview').checked && (reviewedWatermarks.size !== batchJob.watermark_count || checks.summary.warning) && !confirm(`تمت مراجعة ${reviewedWatermarks.size} من ${batchJob.watermark_count} علامة. هل تريد بدء التصدير؟`)) return;
    batchProcessing = true; button.disabled = true; button.querySelector('span').textContent = `جارٍ إنشاء ${batchJob.output_count} ملف PDF…`;
    const form = new FormData(); appendRenderingSettings(form);
    form.append('page_mode', document.querySelector('input[name=batchPages]:checked').value); form.append('custom_pages', $('batchCustomPages').value);
    form.append('filename_mode', $('batchFilenameMode').value); form.append('strict_review', $('batchStrictReview').checked);
    const result = await api(`/api/batch/jobs/${batchJob.id}/generate`, {method: 'POST', body: form});
    if (result.state === 'ready') finishBatch(result);
    else {
      showBatchStatus({percent: result.percent || 0, current: result.current || 0, total: result.total || batchJob.output_count, message: result.message || 'تمت إضافة الدفعة إلى قائمة المعالجة'});
      await pollBatchStatus(result.status_url || `/api/batch/jobs/${batchJob.id}/status`);
    }
  } catch (error) {
    batchProcessing = false; showError('batchErrorBox', error.message); updateReviewSummary(); button.querySelector('span').textContent = 'إنشاء ملفات الدفعة';
  }
});
$('batchDeleteButton').addEventListener('click', () => deleteJob(batchJob));

async function restoreBatchJob() {
  const savedId = localStorage.getItem('watermarkBatchJobId');
  if (!savedId) return;
  try {
    batchJob = await api(`/api/batch/jobs/${savedId}`);
    setMode('batch'); displayBatchJob();
    try {
      const status = await api(`/api/batch/jobs/${savedId}/status`);
      if (status.state === 'ready') finishBatch(status);
      else if (status.state === 'queued' || status.state === 'processing') {
        batchProcessing = true; $('generateBatchButton').disabled = true;
        showBatchStatus(status);
        pollBatchStatus(`/api/batch/jobs/${savedId}/status`);
      } else if (status.state === 'failed') {
        showBatchStatus(status);
        showError('batchErrorBox', status.error || 'فشلت معالجة الدفعة.');
      }
    } catch {}
  } catch {
    localStorage.removeItem('watermarkBatchJobId');
  }
}
restoreBatchJob();
