import fs from "node:fs/promises";
import path from "node:path";
import { SpreadsheetFile, Workbook } from "@oai/artifact-tool";

const root = path.resolve(".");
const matchId = process.argv[2] || "8842468101";
const suffix = process.argv[3] || "latest_pull_rules";
const eventsPath = path.join(root, "computed_events", `fact_events_${matchId}.json`);
const outputDir = path.join(root, "pipeline_excels");
const outputPath = path.join(outputDir, `pull_events_${matchId}_${suffix}.xlsx`);
const previewPath = path.join(outputDir, `pull_events_${matchId}_${suffix}.png`);

const LABEL_PULL = "\u62c9\u91ce";
const KEY_RESULT = "\u7ed3\u679c";
const KEY_NOTE = "\u6279\u6ce8";
const SHEET_NAME = "\u62c9\u91ce\u4e8b\u4ef6";

const allEvents = JSON.parse(await fs.readFile(eventsPath, "utf8"));
const pullEvents = allEvents
  .filter((event) => event.labels === LABEL_PULL)
  .map((event, index) => ({
    ...event,
    id: index + 1,
  }));

const workbook = Workbook.create();
const sheet = workbook.worksheets.add(SHEET_NAME);
sheet.showGridLines = false;

const headers = ["id", "match_id", "labels", "time_range", "heroes", KEY_RESULT, "evidence", KEY_NOTE];
const rows = pullEvents.map((event) => [
  event.id,
  event.match_id,
  event.labels,
  event.time_range,
  event.heroes,
  event[KEY_RESULT] || "",
  event.evidence || "",
  event[KEY_NOTE] || "",
]);

sheet.getRange("A1:H1").values = [headers];
if (rows.length > 0) {
  sheet.getRangeByIndexes(1, 0, rows.length, headers.length).values = rows;
}

const usedRows = Math.max(rows.length + 1, 2);
sheet.getRange(`A1:H${usedRows}`).format.font = { name: "Microsoft YaHei", size: 10 };
sheet.getRange("A1:H1").format = {
  fill: "#1F4E78",
  font: { bold: true, color: "#FFFFFF", name: "Microsoft YaHei", size: 11 },
  horizontalAlignment: "center",
  verticalAlignment: "center",
};
sheet.getRange(`A1:H${usedRows}`).format.borders = { preset: "all", style: "thin", color: "#D9E2F3" };
sheet.getRange(`E2:H${usedRows}`).format.wrapText = true;
sheet.getRange(`A2:H${usedRows}`).format.verticalAlignment = "top";
sheet.getRange("A:A").format.columnWidth = 8;
sheet.getRange("B:B").format.columnWidth = 16;
sheet.getRange("C:C").format.columnWidth = 12;
sheet.getRange("D:D").format.columnWidth = 18;
sheet.getRange("E:E").format.columnWidth = 48;
sheet.getRange("F:F").format.columnWidth = 88;
sheet.getRange("G:G").format.columnWidth = 120;
sheet.getRange("H:H").format.columnWidth = 28;
sheet.freezePanes.freezeRows(1);
sheet.tables.add(`A1:H${usedRows}`, true, "PullEventsTable");

const tableCheck = await workbook.inspect({
  kind: "table",
  sheetId: SHEET_NAME,
  range: `A1:H${Math.min(usedRows, 12)}`,
  include: "values",
  tableMaxRows: 12,
  tableMaxCols: 8,
  maxChars: 4000,
});
console.log(tableCheck.ndjson);

const errors = await workbook.inspect({
  kind: "match",
  searchTerm: "#REF!|#DIV/0!|#VALUE!|#NAME\\?|#N/A",
  options: { useRegex: true, maxResults: 100 },
  summary: "formula error scan",
});
console.log(errors.ndjson);

await fs.mkdir(outputDir, { recursive: true });
const preview = await workbook.render({
  sheetName: SHEET_NAME,
  range: `A1:H${Math.min(usedRows, 12)}`,
  scale: 1,
  format: "png",
});
await fs.writeFile(previewPath, new Uint8Array(await preview.arrayBuffer()));

const xlsx = await SpreadsheetFile.exportXlsx(workbook);
await xlsx.save(outputPath);

console.log(JSON.stringify({ outputPath, previewPath, rows: pullEvents.length }, null, 2));
