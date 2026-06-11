import fs from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { FileBlob, SpreadsheetFile, Workbook } from "@oai/artifact-tool";

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const matchId = process.argv[2];
const seq = process.argv[3] ?? "";
const variant = process.argv[4] ? `_${process.argv[4]}` : "";
const definitionsOverride = process.argv[5];
const eventsOverride = process.argv[6];
if (!matchId) {
  throw new Error("Usage: node build_tactical_table_v1_5.js.mjs <match_id> [seq] [variant] [definitionsPath] [eventsPath]");
}

const outputDir = path.join(root, `outputs/tactical_events_${matchId}`);
const definitionsPath = definitionsOverride ?? path.join(root, "outputs/dota2_tactical_event_definitions_v1.5_20260608.xlsx");
const eventsPath = eventsOverride ?? path.join(outputDir, `tactical_events_${matchId}_event_table_clean_v1.5.json`);
const outputPath = path.join(outputDir, `tactical_events_${matchId}_v1.5${variant}_tactical_table.xlsx`);
const previewPath = path.join(outputDir, `tactical_events_${matchId}_v1.5${variant}_tactical_table_preview.png`);
const definitionVersion = path.basename(definitionsPath).match(/v\d+\.\d+/)?.[0] ?? "v1.5";

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

function countLabels(rows) {
  const counts = new Map();
  for (const row of rows) {
    for (const label of splitLabels(row.labels)) {
      counts.set(label, (counts.get(label) ?? 0) + 1);
    }
  }
  return [...counts.entries()].sort((a, b) => b[1] - a[1] || a[0].localeCompare(b[0], "zh-Hans-CN"));
}

function writeSheet(sheet, headers, rows, widths) {
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

  for (let i = 0; i < widths.length; i += 1) {
    sheet.getRange(`${colLetter(i)}:${colLetter(i)}`).format.columnWidthPx = widths[i];
  }
  sheet.freezePanes.freezeRows(1);
  sheet.showGridLines = false;
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
const headers = formatRows.slice(1).map((row) => row[0]).filter(Boolean);
const expectedHeaders = ["id", "match_id", "labels", "confidence", "time_range", "heroes", "结果", "批注"];
if (JSON.stringify(headers) !== JSON.stringify(expectedHeaders)) {
  throw new Error(`Unexpected output headers from definitions: ${JSON.stringify(headers)}`);
}

const rows = JSON.parse(await fs.readFile(eventsPath, "utf8"));
for (const row of rows) {
  for (const label of splitLabels(row.labels)) {
    if (!allowedLabels.has(label)) {
      throw new Error(`Label "${label}" is not in v1.5 definitions`);
    }
  }
  row["批注"] = row["批注"] ?? "";
}

const workbook = Workbook.create();
writeSheet(workbook.worksheets.add("战术事件"), headers, rows, [54, 112, 150, 88, 120, 390, 620, 160]);
writeSheet(
  workbook.worksheets.add("标签计数"),
  ["标签", "数量", "批注"],
  countLabels(rows).map(([label, count]) => ({ 标签: label, 数量: count, 批注: "" })),
  [160, 90, 220],
);
writeSheet(
  workbook.worksheets.add("定义版本"),
  ["项目", "内容", "批注"],
  [
    { 项目: "定义文件", 内容: path.basename(definitionsPath), 批注: "" },
    { 项目: "定义版本", 内容: definitionVersion, 批注: "" },
    { 项目: "比赛", 内容: `match_id=${matchId}${seq ? `，比赛列表序号 ${seq}` : ""}`, 批注: "" },
    { 项目: "事件数", 内容: rows.length, 批注: "" },
    { 项目: "输出列", 内容: headers.join(", "), 批注: "" },
  ],
  [160, 680, 220],
);

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
  definitionLabels: allowedLabels.size,
  headers,
  formulaErrorScan: formulaErrors.ndjson,
}, null, 2));
