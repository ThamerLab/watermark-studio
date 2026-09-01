const $ = id => document.getElementById(id);
let job = null;
let imageFile = null;
let imageUrl = null;
let batchJob = null;
let batchFiles = [];
let batchCsvFile = null;

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
async function deleteJob(current) { if (!current) return; try { await api(`/api/jobs/${current.id}`, {method: 'DELETE'}); } catch {} location.reload(); }
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
    $('batchSetup').classList.add('hidden'); $('batchWorkspace').classList.remove('hidden');
    $('batchImported').textContent = `تم استيراد ${batchJob.watermark_count} علامات مائية`;
    $('batchSourcesSummary').textContent = `${batchJob.source_count} ملفات مصدر · ${formatBytes(batchJob.total_size)}`;
    $('batchEquation').textContent = `${batchJob.source_count} ملفات × ${batchJob.watermark_count} علامات مائية = ${batchJob.output_count} ملف PDF`;
    $('batchSampleText').textContent = batchJob.watermarks[0].watermark_text;
    $('batchPageCount').textContent = `${batchJob.sources[0].page_count} صفحة`;
    $('batchPagePreview').src = `${batchJob.preview_url}?v=${Date.now()}`;
    updateBatchPreview();
  } catch (error) { showError('batchSetupError', error.message); $('prepareBatchButton').disabled = false; }
  finally { $('batchProgress').classList.add('hidden'); }
});
function batchSettings() {
  return {text: batchJob ? batchJob.watermarks[0].watermark_text : '', fontName: $('batchFontName').value, fontSize: Number($('batchFontSize').value), opacity: Number($('batchOpacity').value), rotation: Number($('batchRotation').value), color: $('batchColor').value, position: $('batchPosition').value};
}
function updateBatchPreview() { updateTextPreview($('batchPreviewCanvas'), $('batchTextOverlay'), batchSettings()); }
[
  ['batchFontSize', 'batchFontSizeOut', value => value], ['batchOpacity', 'batchOpacityOut', value => `${value}%`],
  ['batchRotation', 'batchRotationOut', value => `${value}°`]
].forEach(([id, output, format]) => $(id).addEventListener('input', () => { $(output).value = format($(id).value); updateBatchPreview(); }));
['batchFontName', 'batchColor', 'batchPosition'].forEach(id => $(id).addEventListener('input', updateBatchPreview));
document.querySelectorAll('input[name=batchPages]').forEach(input => input.addEventListener('change', () => $('batchCustomPagesWrap').classList.toggle('hidden', input.value !== 'custom' || !input.checked)));
$('generateBatchButton').addEventListener('click', async () => {
  clearError('batchErrorBox'); $('batchResultBox').classList.add('hidden');
  const button = $('generateBatchButton'); button.disabled = true; button.querySelector('span').textContent = `جارٍ إنشاء ${batchJob.output_count} ملف PDF…`;
  const settings = batchSettings(); const form = new FormData();
  form.append('font_name', settings.fontName); form.append('font_size', settings.fontSize); form.append('opacity', settings.opacity / 100);
  form.append('rotation', settings.rotation); form.append('color', settings.color); form.append('position', settings.position);
  form.append('page_mode', document.querySelector('input[name=batchPages]:checked').value); form.append('custom_pages', $('batchCustomPages').value);
  try {
    const result = await api(`/api/batch/jobs/${batchJob.id}/generate`, {method: 'POST', body: form});
    $('batchResultText').textContent = `تم إنشاء ${result.generated_count} ملف PDF بنجاح`;
    $('batchDownloadLink').href = result.download_url; $('batchResultBox').classList.remove('hidden');
  } catch (error) { showError('batchErrorBox', error.message); }
  finally { button.disabled = false; button.querySelector('span').textContent = 'إنشاء ملفات الدفعة'; }
});
$('batchDeleteButton').addEventListener('click', () => deleteJob(batchJob));
