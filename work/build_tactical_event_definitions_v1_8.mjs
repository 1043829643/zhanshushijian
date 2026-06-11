import fs from "node:fs/promises";
import path from "node:path";
import { FileBlob, SpreadsheetFile, Workbook } from "@oai/artifact-tool";

const root = "C:/Users/Di/Documents/Codex/2026-06-08/files-mentioned-by-the-user-blast";
const outputDir = path.join(root, "outputs");
const sourcePath = path.join(outputDir, "dota2_tactical_event_definitions_v1.7_20260609.xlsx");
const xlsxPath = path.join(outputDir, "dota2_tactical_event_definitions_v1.8_20260609.xlsx");
const mdPath = path.join(outputDir, "dota2_tactical_event_definitions_v1.8_20260609.md");
const previewPath = path.join(outputDir, "dota2_tactical_event_definitions_v1.8_preview.png");

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
    tableMaxRows: 180,
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
const versionTable = await readTable(sourceWorkbook, "版本说明!A1:B100");
const definitionsTable = await readTable(sourceWorkbook, "事件定义!A1:H110");
const globalRulesTable = await readTable(sourceWorkbook, "全局规则!A1:F150");
const outputFormatTable = await readTable(sourceWorkbook, "输出格式!A1:E30");

const versionHeaders = versionTable[0];
const versionRows = versionTable.slice(1).map((row) => [...row]);
for (const row of versionRows) {
  if (row[0] === "版本") row[1] = "v1.8";
  if (row[0] === "生成日期") row[1] = "2026-06-09";
}
versionRows.push(["v1.8 变更 1", "新增“肉山击杀”标签：所有肉山击杀不再作为普通资源获得输出，统一以肉山击杀事件记录。"]);
versionRows.push(["v1.8 变更 2", "肉山击杀结果列必须写清击杀阵营，以及同一波肉山掉落的获得英雄和资源类型，例如盾、奶酪、刷新碎片、战旗等；相邻掉落行并入肉山击杀事件。"]);
versionRows.push(["v1.8 变更 3", "新增相邻近距战斗去重：时间重叠或间隔极短、位置相近、参战英雄高度重合且无独立击杀结果的战斗候选，合并为一个事件。"]);

const definitionHeaders = definitionsTable[0];
const sourceDefinitionRows = definitionsTable.slice(1);
const definitionRows = [];
for (const row of sourceDefinitionRows) {
  definitionRows.push(row);
  if (row[0] === "肉山团" && !sourceDefinitionRows.some((item) => item[0] === "肉山击杀")) {
    definitionRows.push([
      "肉山击杀",
      "新增",
      "A：数据库可直接生成",
      "肉山被击杀的资源事件。该事件按阵营归属记录，并合并同一波肉山掉落的获得结果。",
      "combat_logs roshan_death；match_chat_events ROSHAN_KILL、AEGIS、CHEESE、REFRESHER_SHARD、BANNER_PLANTED 等。",
      "labels=肉山击杀；heroes 输出获得掉落的英雄；结果输出某方击杀肉山以及各英雄获得的盾、奶酪、刷新碎片、战旗等。",
      "不再误用 ROSHAN_KILL 的 player/value 字段作为击杀英雄。",
      "",
    ]);
  }
}

const globalRuleHeaders = globalRulesTable[0];
const globalRuleRows = globalRulesTable.slice(1);
globalRuleRows.push([
  "资源",
  "肉山击杀独立标签",
  "肉山击杀从资源获得中拆出，统一记录为“肉山击杀”。同一波肉山掉落在击杀后短时间内出现时，合并入该肉山击杀事件，不再单独输出为资源获得。",
  "肉山击杀、资源获得",
  "结果列必须包括击杀阵营及掉落获得者，例如“夜魇击杀肉山；风暴之灵获得肉山盾”。",
  "",
]);
globalRuleRows.push([
  "战斗归类",
  "相邻近距战斗去重",
  "两个战斗候选若时间重叠或间隔不超过 3 秒、位置距离不超过约 1800 码、参战英雄 Jaccard 重合度至少 50% 或重合英雄不少于 2 人，且双方均无独立击杀结果，则视为同一战斗事件并合并。",
  "小规模冲突、团战、肉山团、高地团、守塔团、魔晶团",
  "合并后的 time_range 覆盖全部候选窗口，heroes 取双方英雄并集，结果记为未记录英雄击杀/死亡无。",
  "",
]);
const outputFormatHeaders = outputFormatTable[0];
const outputFormatRows = outputFormatTable.slice(1);

const workbook = Workbook.create();
writeSheet(workbook.worksheets.add("版本说明"), versionHeaders, versionRows, [160, 980]);
writeSheet(workbook.worksheets.add("事件定义"), definitionHeaders, definitionRows, [110, 100, 140, 420, 360, 390, 300, 160]);
writeSheet(workbook.worksheets.add("全局规则"), globalRuleHeaders, globalRuleRows, [120, 160, 560, 260, 430, 160]);
writeSheet(workbook.worksheets.add("输出格式"), outputFormatHeaders, outputFormatRows, [120, 260, 90, 520, 160]);

const preview = await workbook.render({ sheetName: "事件定义", range: "A1:H22", scale: 1, format: "png" });
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
  "# Dota2 战术事件定义清单 v1.8",
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
  version: "v1.8",
  definitions: definitionLabels.length,
  hasRoshanKillLabel: definitionLabels.includes("肉山击杀"),
  hasRemovedLabel: definitionLabels.includes("抓人"),
  containsRemovedText: allText.includes("抓人"),
  formulaErrorScan: formulaErrors.ndjson,
}, null, 2));
