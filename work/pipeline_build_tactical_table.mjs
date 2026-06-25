import fs from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { FileBlob, SpreadsheetFile, Workbook } from "@oai/artifact-tool";

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const matchId = process.argv[2];
const seq = process.argv[3] ?? "";
const computedDir = process.argv[4] ?? path.join(root, "computed_events");
const definitionsPath = process.argv[5] ?? path.join(root, "definitions", "dota2_tactical_event_definitions_v1.9_20260609.xlsx");
const outputSuffix = process.argv[6] ? `_${process.argv[6]}` : "";

if (!matchId) {
  throw new Error("Usage: node pipeline_build_tactical_table.mjs <match_id> [seq] [computedDir] [definitionsPath] [outputSuffix]");
}

const outputDir = path.join(root, "pipeline_excels");
const factPath = path.join(computedDir, `fact_events_${matchId}.json`);
const fightPath = path.join(computedDir, `fight_events_${matchId}.json`);
const outputPath = path.join(outputDir, `战术事件_${matchId}_pipeline${outputSuffix}.xlsx`);
const previewPath = path.join(outputDir, `战术事件_${matchId}_pipeline${outputSuffix}_preview.png`);

const RESULT_HEADER = "\u7ed3\u679c";
const NOTE_HEADER = "\u6279\u6ce8";
const expectedHeaders = ["id", "match_id", "labels", "time_range", "heroes", RESULT_HEADER, NOTE_HEADER];

function colLetter(index) {
  let n = index + 1;
  let text = "";
  while (n > 0) {
    const mod = (n - 1) % 26;
    text = String.fromCharCode(65 + mod) + text;
    n = Math.floor((n - mod - 1) / 26);
  }
  return text;
}

function splitLabels(value) {
  return String(value ?? "").split(/\s+\/\s+/).map((label) => label.trim()).filter(Boolean);
}

function parseTimeText(value) {
  const match = String(value ?? "").match(/^(-?)(\d+):(\d+)$/);
  if (!match) return 0;
  const seconds = Number(match[2]) * 60 + Number(match[3]);
  return match[1] ? -seconds : seconds;
}

function rangeStartSeconds(row) {
  const match = String(row.time_range ?? "00:00-00:00").match(/^(-?\d+:\d+)-/);
  return parseTimeText(match?.[1] ?? "00:00");
}

function countLabels(rows) {
  const counts = new Map();
  for (const row of rows) {
    for (const label of splitLabels(row.labels)) {
      counts.set(label, (counts.get(label) ?? 0) + 1);
    }
  }
  return [...counts.entries()].sort((a, b) => b[1] - a[1] || a[0].localeCompare(b[0], "zh-Hans-CN"));
}

function writeSheet(sheet, headers, rows, widths, rowHeightPx = 54) {
  const matrix = [headers, ...rows.map((row) => headers.map((header) => row[header] ?? ""))];
  const range = sheet.getRangeByIndexes(0, 0, matrix.length, headers.length);
  range.values = matrix;
  range.format.font.name = "Microsoft YaHei";
  range.format.font.size = 10;
  range.format.wrapText = true;
  range.format.verticalAlignment = "Top";
  range.format.borders = { preset: "all", style: "thin", color: "#BFBFBF" };

  const headerRange = sheet.getRangeByIndexes(0, 0, 1, headers.length);
  headerRange.format.fill.color = "#1F4E78";
  headerRange.format.font.color = "#FFFFFF";
  headerRange.format.font.bold = true;
  headerRange.format.horizontalAlignment = "Center";
  headerRange.format.rowHeightPx = 30;

  if (rows.length > 0) {
    sheet.getRangeByIndexes(1, 0, rows.length, headers.length).format.rowHeightPx = rowHeightPx;
  }

  for (let i = 0; i < widths.length; i += 1) {
    sheet.getRange(`${colLetter(i)}:${colLetter(i)}`).format.columnWidthPx = widths[i];
  }
  sheet.freezePanes.freezeRows(1);
  sheet.showGridLines = false;
}

async function readJsonArray(filePath) {
  try {
    return JSON.parse(await fs.readFile(filePath, "utf8"));
  } catch (error) {
    if (error.code === "ENOENT") return [];
    throw error;
  }
}

function normalizeRows(rows) {
  return rows.map((row) => {
    const out = {};
    for (const header of expectedHeaders) {
      out[header] = row[header] ?? "";
    }
    out.match_id = String(out.match_id || matchId);
    out[RESULT_HEADER] = row[RESULT_HEADER] ?? row["\u7f01\u6496\u7049"] ?? "";
    out[NOTE_HEADER] = "";
    return out;
  });
}

const definitionWorkbook = await SpreadsheetFile.importXlsx(await FileBlob.load(definitionsPath));
const eventDefinitions = await definitionWorkbook.inspect({
  kind: "table",
  range: "事件定义!A1:H80",
  include: "values",
  tableMaxRows: 80,
  tableMaxCols: 8,
});
const outputFormat = await definitionWorkbook.inspect({
  kind: "table",
  range: "输出格式!A1:E20",
  include: "values",
  tableMaxRows: 20,
  tableMaxCols: 5,
});
const definitionRows = eventDefinitions.ndjson.split("\n").filter(Boolean).map((line) => JSON.parse(line)).find((row) => row.kind === "table")?.values ?? [];
const formatRows = outputFormat.ndjson.split("\n").filter(Boolean).map((line) => JSON.parse(line)).find((row) => row.kind === "table")?.values ?? [];
const allowedLabels = new Set(definitionRows.slice(1).map((row) => row[0]).filter(Boolean));
allowedLabels.add("出门抢远程兵");
allowedLabels.add("出门抢近战兵");
const definitionHeaders = formatRows.slice(1).map((row) => row[0]).filter(Boolean);
const supportedDefinitionHeaders = ["id", "match_id", "labels", "confidence", "time_range", "heroes", RESULT_HEADER, NOTE_HEADER];
if (JSON.stringify(definitionHeaders) !== JSON.stringify(supportedDefinitionHeaders)) {
  throw new Error(`Unexpected output headers from definitions: ${JSON.stringify(definitionHeaders)}`);
}

const factRows = normalizeRows(await readJsonArray(factPath));
const fightRows = normalizeRows(await readJsonArray(fightPath));
let rows = [...factRows, ...fightRows].sort((a, b) => rangeStartSeconds(a) - rangeStartSeconds(b) || String(a.labels).localeCompare(String(b.labels), "zh-Hans-CN"));
rows = rows.map((row, index) => ({ ...row, id: index + 1 }));

const labelsMissingFromDefinitions = new Set();
for (const row of rows) {
  for (const label of splitLabels(row.labels)) {
    if (!allowedLabels.has(label)) {
      labelsMissingFromDefinitions.add(label);
    }
  }
}

const workbook = Workbook.create();
writeSheet(workbook.worksheets.add("战术事件"), expectedHeaders, rows, [54, 112, 150, 120, 390, 620, 160], 58);
writeSheet(
  workbook.worksheets.add("标签计数"),
  ["标签", "数量", "批注"],
  countLabels(rows).map(([label, count]) => ({ 标签: label, 数量: count, 批注: "" })),
  [160, 90, 220],
  34,
);

const definitionVersion = path.basename(definitionsPath).match(/v\d+\.\d+/)?.[0] ?? "unknown";
writeSheet(
  workbook.worksheets.add("定义版本"),
  ["项目", "内容", "批注"],
  [
    { 项目: "定义文件", 内容: path.basename(definitionsPath), 批注: "" },
    { 项目: "定义版本", 内容: definitionVersion, 批注: "" },
    { 项目: "比赛", 内容: `match_id=${matchId}${seq ? `，比赛列表序号 ${seq}` : ""}`, 批注: "" },
    { 项目: "事件数", 内容: rows.length, 批注: "" },
    { 项目: "输出列", 内容: expectedHeaders.join(", "), 批注: "" },
    { 项目: "数据来源", 内容: `pipeline: ${path.basename(factPath)} + ${path.basename(fightPath)}`, 批注: "" },
    { 项目: "定义表未列出标签", 内容: [...labelsMissingFromDefinitions].join("、") || "无", 批注: "" },
  ],
  [160, 760, 220],
  34,
);

await fs.mkdir(outputDir, { recursive: true });
const formulaErrors = await workbook.inspect({
  kind: "match",
  searchTerm: "#REF!|#DIV/0!|#VALUE!|#NAME\\?|#N/A",
  options: { useRegex: true, maxResults: 100 },
  summary: "formula error scan",
});
const preview = await workbook.render({
  sheetName: "战术事件",
  range: "A1:H28",
  scale: 1,
  format: "png",
});
await fs.writeFile(previewPath, new Uint8Array(await preview.arrayBuffer()));

const output = await SpreadsheetFile.exportXlsx(workbook);
await output.save(outputPath);

console.log(JSON.stringify({
  outputPath,
  previewPath,
  rows: rows.length,
  factRows: factRows.length,
  fightRows: fightRows.length,
  definitionLabels: allowedLabels.size,
  labelsMissingFromDefinitions: [...labelsMissingFromDefinitions].sort((a, b) => a.localeCompare(b, "zh-Hans-CN")),
  labelCounts: Object.fromEntries(countLabels(rows)),
  formulaErrorScan: formulaErrors.ndjson,
}, null, 2));
