import fs from "node:fs/promises";
import path from "node:path";
import { FileBlob, SpreadsheetFile, Workbook } from "@oai/artifact-tool";

const root = "C:/Users/Di/Documents/Codex/2026-06-08/files-mentioned-by-the-user-blast";
const outputDir = path.join(root, "outputs");
const sourcePath = path.join(outputDir, "dota2_tactical_event_definitions_v1.8_20260609.xlsx");
const xlsxPath = path.join(outputDir, "dota2_tactical_event_definitions_v1.9_20260609.xlsx");
const mdPath = path.join(outputDir, "dota2_tactical_event_definitions_v1.9_20260609.md");
const previewPath = path.join(outputDir, "dota2_tactical_event_definitions_v1.9_preview.png");

function parseTable(ndjson) {
  return ndjson
    .split("\n")
    .filter(Boolean)
    .map((line) => JSON.parse(line))
    .find((row) => row.kind === "table")?.values ?? [];
}

async function readTable(workbook, range) {
  const result = await workbook.inspect({
    kind: "table",
    range,
    include: "values",
    tableMaxRows: 200,
    tableMaxCols: 12,
  });
  return parseTable(result.ndjson).filter((row) =>
    row.some((cell) => cell !== null && cell !== undefined && String(cell).trim() !== "")
  );
}

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

function writeSheet(sheet, headers, rows, widths) {
  const matrix = [headers, ...rows];
  const range = sheet.getRangeByIndexes(0, 0, matrix.length, headers.length);
  range.values = matrix;
  range.format.font.name = "Microsoft YaHei";
  range.format.font.size = 10;
  range.format.wrapText = true;
  range.format.verticalAlignment = "Top";
  range.format.borders = { preset: "all", style: "thin", color: "#BFBFBF" };

  const header = sheet.getRangeByIndexes(0, 0, 1, headers.length);
  header.format.fill.color = "#1F4E78";
  header.format.font.color = "#FFFFFF";
  header.format.font.bold = true;
  header.format.horizontalAlignment = "Center";
  header.format.rowHeightPx = 32;

  for (let i = 0; i < widths.length; i += 1) {
    sheet.getRange(`${colLetter(i)}:${colLetter(i)}`).format.columnWidthPx = widths[i];
  }
  sheet.freezePanes.freezeRows(1);
  sheet.showGridLines = false;
}

function markdownTable(headers, rows) {
  const escapeCell = (value) => String(value ?? "").replace(/\|/g, "/").replace(/\n/g, " ");
  return [
    `| ${headers.map(escapeCell).join(" | ")} |`,
    `| ${headers.map(() => "---").join(" | ")} |`,
    ...rows.map((row) => `| ${row.map(escapeCell).join(" | ")} |`),
  ].join("\n");
}

const sourceWorkbook = await SpreadsheetFile.importXlsx(await FileBlob.load(sourcePath));
const versionTable = await readTable(sourceWorkbook, "版本说明!A1:B120");
const definitionsTable = await readTable(sourceWorkbook, "事件定义!A1:H120");
const globalRulesTable = await readTable(sourceWorkbook, "全局规则!A1:F180");
const outputFormatTable = await readTable(sourceWorkbook, "输出格式!A1:E30");

const versionHeaders = versionTable[0];
const versionRows = versionTable.slice(1).map((row) => [...row]);
for (const row of versionRows) {
  if (row[0] === "版本") row[1] = "v1.9";
  if (row[0] === "生成日期") row[1] = "2026-06-09";
}
versionRows.push(["v1.9 变更 1", "继续检查时间相邻战斗事件，新增“有击杀父事件吸收无独立击杀短窗口”规则。"]);
versionRows.push(["v1.9 错误原因", "v1.8 只合并了无击杀短窗口之间的重复，漏掉了父事件已记录完整击杀/死亡、相邻短窗口仅记录 BKB/TP/短控制/短伤害的情况，导致同一战斗仍拆成两行。"]);

const definitionHeaders = definitionsTable[0];
const definitionRows = definitionsTable.slice(1);
const globalRuleHeaders = globalRulesTable[0];
const globalRuleRows = globalRulesTable.slice(1);
globalRuleRows.push([
  "战斗归类",
  "有击杀父事件吸收短窗口",
  "若一个战斗事件已经记录击杀/死亡，另一个相邻或重叠短窗口无独立击杀结果，且二者位置距离不超过约 2500 码、共享英雄不少于 2 人或英雄重合度达到 33%，则短窗口并入有击杀父事件。",
  "小规模冲突、团战、肉山团、高地团、守塔团、魔晶团",
  "保留父事件击杀结果；time_range 覆盖父子窗口；heroes 取双方英雄并集；删除无独立击杀短窗口独立行。",
  "",
]);
globalRuleRows.push([
  "质量控制",
  "相邻战斗复查顺序",
  "生成表格后需按战斗时间序列复查相邻 10 秒内的战斗事件。若出现共享英雄、同区域、同一击杀链或逃脱动作与父事件重叠，应优先判断是否为同一战斗被重复识别。",
  "所有战斗类标签",
  "防止 BKB、TP、短控制、短伤害等微事件被单独保留为战术事件。",
  "",
]);
const outputFormatHeaders = outputFormatTable[0];
const outputFormatRows = outputFormatTable.slice(1);

const workbook = Workbook.create();
writeSheet(workbook.worksheets.add("版本说明"), versionHeaders, versionRows, [160, 980]);
writeSheet(workbook.worksheets.add("事件定义"), definitionHeaders, definitionRows, [110, 100, 140, 420, 360, 390, 300, 160]);
writeSheet(workbook.worksheets.add("全局规则"), globalRuleHeaders, globalRuleRows, [120, 160, 580, 260, 430, 160]);
writeSheet(workbook.worksheets.add("输出格式"), outputFormatHeaders, outputFormatRows, [120, 260, 90, 520, 160]);

const preview = await workbook.render({ sheetName: "全局规则", range: "A1:F24", scale: 1, format: "png" });
await fs.writeFile(previewPath, new Uint8Array(await preview.arrayBuffer()));

const formulaErrors = await workbook.inspect({
  kind: "match",
  searchTerm: "#REF!|#DIV/0!|#VALUE!|#NAME\\?|#N/A",
  options: { useRegex: true, maxResults: 100 },
  summary: "formula error scan",
});

const exported = await SpreadsheetFile.exportXlsx(workbook);
await exported.save(xlsxPath);

const md = [
  "# Dota2 战术事件定义清单 v1.9",
  "",
  "## 版本说明",
  "",
  markdownTable(versionHeaders, versionRows),
  "",
  "## 事件定义",
  "",
  markdownTable(definitionHeaders, definitionRows),
  "",
  "## 全局规则",
  "",
  markdownTable(globalRuleHeaders, globalRuleRows),
  "",
  "## 输出格式",
  "",
  markdownTable(outputFormatHeaders, outputFormatRows),
  "",
].join("\n");
await fs.writeFile(mdPath, md, "utf8");

const definitionLabels = definitionRows.map((row) => row[0]).filter(Boolean);
const allText = [versionRows, definitionRows, globalRuleRows].flat(2).join("\n");
console.log(JSON.stringify({
  xlsxPath,
  mdPath,
  previewPath,
  version: "v1.9",
  definitions: definitionLabels.length,
  hasRoshanKillLabel: definitionLabels.includes("肉山击杀"),
  hasRemovedLabel: definitionLabels.includes("抓人"),
  containsRemovedText: allText.includes("抓人"),
  formulaErrorScan: formulaErrors.ndjson,
}, null, 2));
