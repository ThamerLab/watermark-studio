import { readFile, writeFile } from "node:fs/promises";
import { resolve } from "node:path";
import { build } from "esbuild";

const root = resolve(import.meta.dirname, "..");
const src = resolve(import.meta.dirname, "src");
const read = (path) => readFile(resolve(root, path), "utf8");

const pdfJsBundle = build({
  entryPoints: [resolve(root, "node_modules/pdfjs-dist/build/pdf.mjs")],
  bundle: true,
  write: false,
  minify: true,
  format: "iife",
  globalName: "pdfjsLib",
  platform: "browser",
  target: ["chrome120", "edge120"],
});

const [template, styles, app, pdfLib, pdfJsBuild, pdfWorker, jsZip, papaParse, font] = await Promise.all([
  readFile(resolve(src, "index.html"), "utf8"),
  readFile(resolve(src, "styles.css"), "utf8"),
  readFile(resolve(src, "app.js"), "utf8"),
  read("node_modules/pdf-lib/dist/pdf-lib.min.js"),
  pdfJsBundle,
  readFile(resolve(root, "node_modules/pdfjs-dist/build/pdf.worker.min.mjs")),
  read("node_modules/jszip/dist/jszip.min.js"),
  read("node_modules/papaparse/papaparse.min.js"),
  readFile(resolve(root, "node_modules/@fontsource/noto-sans-arabic/files/noto-sans-arabic-arabic-400-normal.woff2")),
]);
const pdfJs = pdfJsBuild.outputFiles[0].text;

const safeScript = (value) => value.replaceAll("</script", "<\\/script");
const inject = (value, marker, content) => value.replace(marker, () => content);
let output = template;
output = inject(output, "/*__STYLES__*/", styles);
output = inject(output, "/*__NOTO_FONT__*/", font.toString("base64"));
output = inject(output, "/*__PDF_LIB__*/", safeScript(pdfLib));
output = inject(output, "/*__PDF_JS__*/", safeScript(pdfJs));
output = inject(output, "/*__JSZIP__*/", safeScript(jsZip));
output = inject(output, "/*__PAPAPARSE__*/", safeScript(papaParse));
output = inject(output, "/*__PDF_WORKER_BASE64__*/", pdfWorker.toString("base64"));
output = inject(output, "/*__APP__*/", safeScript(app));

const unresolved = output.match(/\/\*__(STYLES|NOTO_FONT|PDF_LIB|PDF_JS|JSZIP|PAPAPARSE|PDF_WORKER_BASE64|APP)__\*\//);
if (unresolved) throw new Error(`Unresolved standalone build placeholder: ${unresolved[0]}`);

await writeFile(resolve(root, "watermark-studio.html"), output, "utf8");
console.log(`Built watermark-studio.html (${(Buffer.byteLength(output) / 1024 / 1024).toFixed(2)} MiB)`);
