import { build } from "esbuild";
import JavaScriptObfuscator from "javascript-obfuscator";
import { writeFileSync, mkdirSync } from "node:fs";
import { dirname, basename, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const __dirname = dirname(fileURLToPath(import.meta.url));
const dev = process.argv.includes("--dev");

const entries = ["app.js", "login.js", "landing.js", "admin.js", "status.js", "voice.js"];

mkdirSync(resolve(__dirname, "static/js/bundles"), { recursive: true });

for (const entry of entries) {
  const result = await build({
    entryPoints: [resolve(__dirname, "static/js", entry)],
    bundle: true,
    format: "iife",
    minify: !dev,
    write: false,
    legalComments: "none",
    logLevel: "silent",
  });

  const raw = result.outputFiles[0].text;

  const out = dev
    ? raw
    : JavaScriptObfuscator.obfuscate(raw, {
        compact: true,
        controlFlowFlattening: true,
        controlFlowFlatteningThreshold: 0.5,
        deadCodeInjection: false,
        identifierNamesGenerator: "hexadecimal",
        renameGlobals: false,
        selfDefending: false,
        simplify: true,
        stringArray: true,
        stringArrayEncoding: ["base64"],
        stringArrayThreshold: 0.5,
        transformObjectKeys: false,
      }).getObfuscatedCode();

  const name = basename(entry, ".js") + ".bundle.js";
  writeFileSync(resolve(__dirname, "static/js/bundles", name), out, "utf8");
  console.log(`built static/js/bundles/${name} (${(out.length / 1024).toFixed(1)} KB, ${dev ? "dev" : "obfuscated"})`);
}