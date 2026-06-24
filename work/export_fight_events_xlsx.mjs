import fs from "node:fs/promises";
import path from "node:path";
import { SpreadsheetFile, Workbook } from "@oai/artifact-tool";

const root = path.resolve(".");
const matchId = process.argv[2] || "8842468101";
const suffix = process.argv[3] || "latest_rules";
const eventsPath = path.join(root, "computed_events", `fight_events_${matchId}.json`);
const recordsPath = path.join(root, "computed_events", `fight_records_${matchId}.json`);
const outputDir = path.join(root, "pipeline_excels");
const outputPath = path.join(outputDir, `战斗事件_${matchId}_${suffix}.xlsx`);
const previewPath = path.join(outputDir, `战斗事件_${matchId}_${suffix}_preview.png`);

const events = JSON.parse(await fs.readFile(eventsPath, "utf8"));
const records = JSON.parse(await fs.readFile(recordsPath, "utf8"));

const labelCounts = new Map();
for (const event of events) {
  for (const label of String(event.labels || "").split("/").map((item) => item.trim()).filter(Boolean)) {
    labelCounts.set(label, (labelCounts.get(label) || 0) + 1);
  }
}

const workbook = Workbook.create();
const detail = workbook.worksheets.add("战斗事件");
const summary = workbook.worksheets.add("规则摘要");

detail.showGridLines = false;
summary.showGridLines = false;

const headers = ["序号", "比赛ID", "标签", "置信度", "时间范围", "参与英雄", "结果", "证据", "批注"];
const rows = events.map((event, index) => [
  index + 1,
  event.match_id,
  event.labels,
  event.confidence,
  event.time_range,
  event.heroes,
  event["结果"],
  event.evidence,
  event["批注"] || "",
]);

detail.getRange("A1:I1").values = [headers];
if (rows.length) {
  detail.getRangeByIndexes(1, 0, rows.length, headers.length).values = rows;
}

const usedRows = Math.max(rows.length + 1, 2);
const detailRange = detail.getRange(`A1:I${usedRows}`);
detailRange.format.font = { name: "Microsoft YaHei", size: 10 };
detail.getRange("A1:I1").format = {
  fill: "#1F4E78",
  font: { bold: true, color: "#FFFFFF", name: "Microsoft YaHei", size: 10 },
  horizontalAlignment: "center",
  verticalAlignment: "center",
};
detail.getRange(`A1:I${usedRows}`).format.borders = { preset: "all", style: "thin", color: "#D9E2F3" };
detail.getRange(`F2:H${usedRows}`).format.wrapText = true;
detail.getRange(`A2:E${usedRows}`).format.verticalAlignment = "top";
detail.getRange(`F2:I${usedRows}`).format.verticalAlignment = "top";
detail.getRange("A:A").format.columnWidth = 8;
detail.getRange("B:B").format.columnWidth = 14;
detail.getRange("C:C").format.columnWidth = 24;
detail.getRange("D:D").format.columnWidth = 10;
detail.getRange("E:E").format.columnWidth = 16;
detail.getRange("F:F").format.columnWidth = 46;
detail.getRange("G:G").format.columnWidth = 64;
detail.getRange("H:H").format.columnWidth = 80;
detail.getRange("I:I").format.columnWidth = 18;
detail.getRange(`A2:A${usedRows}`).format.numberFormat = "0";
detail.freezePanes.freezeRows(1);
detail.tables.add(`A1:I${usedRows}`, true, "FightEventsTable");

summary.getRange("A1:D1").values = [["8842468101 最新版战斗事件摘要", "", "", ""]];
summary.getRange("A1:D1").merge();
summary.getRange("A1:D1").format = {
  fill: "#1F4E78",
  font: { bold: true, color: "#FFFFFF", name: "Microsoft YaHei", size: 14 },
  horizontalAlignment: "center",
};

const summaryRows = [
  ["项目", "值", "说明", ""],
  ["战斗事件总数", events.length, "仅包含 fight_events，不含事实类事件", ""],
  ["fight_records 总数", records.length, "重算后的底层战斗聚类记录数", ""],
  ["战斗合并时间", "12s", "相邻信号时间间隔阈值", ""],
  ["战斗合并距离", "2200码", "新信号到尾部动态中心的最大距离", ""],
  ["尾部动态中心", "最近12s / 最多8条信号", "用于跟随边打边走/追击", ""],
  ["远程贡献", "2000码外", "写作英雄（远程），不计入参与人数", ""],
  ["GANK 保留", "被抓目标承伤>=600或死亡", "不再使用双方总伤害", ""],
  ["肉山坑上下文", "坑位2000码内", "团战/小规模冲突可追加肉山团", ""],
  ["拉野贴近阈值", "550码", "本表不含拉野，只记录当前全局规则版本", ""],
];
summary.getRangeByIndexes(2, 0, summaryRows.length, 4).values = summaryRows;
summary.getRange(`A3:D${summaryRows.length + 2}`).format.font = { name: "Microsoft YaHei", size: 10 };
summary.getRange(`A3:D${summaryRows.length + 2}`).format.borders = { preset: "all", style: "thin", color: "#D9E2F3" };
summary.getRange("A3:D3").format = {
  fill: "#D9EAF7",
  font: { bold: true, name: "Microsoft YaHei", size: 10 },
};
summary.getRange("A:A").format.columnWidth = 22;
summary.getRange("B:B").format.columnWidth = 24;
summary.getRange("C:C").format.columnWidth = 56;
summary.getRange("D:D").format.columnWidth = 8;

const countHeaderRow = summaryRows.length + 5;
summary.getRange(`A${countHeaderRow}:B${countHeaderRow}`).values = [["标签", "数量"]];
summary.getRange(`A${countHeaderRow}:B${countHeaderRow}`).format = {
  fill: "#D9EAF7",
  font: { bold: true, name: "Microsoft YaHei", size: 10 },
};
const countRows = [...labelCounts.entries()].sort((a, b) => b[1] - a[1] || a[0].localeCompare(b[0], "zh-Hans"));
if (countRows.length) {
  summary.getRangeByIndexes(countHeaderRow, 0, countRows.length, 2).values = countRows;
  summary.getRange(`A${countHeaderRow}:B${countHeaderRow + countRows.length}`).format.borders = {
    preset: "all",
    style: "thin",
    color: "#D9E2F3",
  };
}

const errors = await workbook.inspect({
  kind: "match",
  searchTerm: "#REF!|#DIV/0!|#VALUE!|#NAME\\?|#N/A",
  options: { useRegex: true, maxResults: 100 },
  summary: "formula error scan",
});
console.log(errors.ndjson);

const tablePreview = await workbook.inspect({
  kind: "table",
  sheetId: "战斗事件",
  range: "A1:I8",
  tableMaxRows: 8,
  tableMaxCols: 9,
  maxChars: 4000,
});
console.log(tablePreview.ndjson);

const preview = await workbook.render({
  sheetName: "战斗事件",
  range: "A1:I12",
  scale: 1,
  format: "png",
});

await fs.mkdir(outputDir, { recursive: true });
await fs.writeFile(previewPath, new Uint8Array(await preview.arrayBuffer()));
const xlsx = await SpreadsheetFile.exportXlsx(workbook);
await xlsx.save(outputPath);

console.log(JSON.stringify({
  outputPath,
  previewPath,
  rows: events.length,
  labelCounts: Object.fromEntries(labelCounts),
}, null, 2));
