import { readFileSync, writeFileSync } from "node:fs";

const html = readFileSync("dist/index.html", "utf8");
if (!html.includes("__TAPESTRY_BUNDLE__")) {
  console.error("Built template lost the data sentinel — check index.html/singlefile config.");
  process.exit(1);
}
writeFileSync("../theloom/viz/static/tapestry.html", html);
console.log("Template emitted to theloom/viz/static/tapestry.html");
