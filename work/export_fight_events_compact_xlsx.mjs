import fs from "node:fs/promises";
import path from "node:path";
import { SpreadsheetFile, Workbook } from "@oai/artifact-tool";

const root = path.resolve(".");
const matchId = process.argv[2] || "8842468101";
const suffix = process.argv[3] || "compact";
const eventsPath = path.join(root, "computed_events", `fight_events_${matchId}.json`);
const outputDir = path.join(root, "pipeline_excels");
const outputPath = path.join(outputDir, `战斗事件_${matchId}_${suffix}.xlsx`);

const events = JSON.parse(await fs.readFile(eventsPath, "utf8"));
const workbook = Workbook.create();
const sheet = workbook.worksheets.add("战斗事件");
sheet.showGridLines = false;

const headers = ["id", "match_id", "labels", "time_range", "heroes", "结果"];
const rows = events.map((event) => [
  event.id,
  event.match_id,
  event.labels,
  event.time_range,
  event.heroes,
  event["结果"],
]);

sheet.getRange("A1:F1").values = [headers];
if (rows.length) {
  sheet.getRangeByIndexes(1, 0, rows.length, headers.length).values = rows;
}

const usedRows = Math.max(rows.length + 1, 2);
sheet.getRange(`A1:F${usedRows}`).format.font = { name: "Microsoft YaHei", size: 10 };
sheet.getRange("A1:F1").format = {
  fill: "#1F4E78",
  font: { bold: true, color: "#FFFFFF", name: "Microsoft YaHei", size: 11 },
  horizontalAlignment: "center",
  verticalAlignment: "center",
};
sheet.getRange(`A1:F${usedRows}`).format.borders = { preset: "all", style: "thin", color: "#D9E2F3" };
sheet.getRange(`E2:F${usedRows}`).format.wrapText = true;
sheet.getRange(`A2:F${usedRows}`).format.verticalAlignment = "top";
sheet.getRange("A:A").format.columnWidth = 8;
sheet.getRange("B:B").format.columnWidth = 16;
sheet.getRange("C:C").format.columnWidth = 26;
sheet.getRange("D:D").format.columnWidth = 18;
sheet.getRange("E:E").format.columnWidth = 64;
sheet.getRange("F:F").format.columnWidth = 96;
sheet.freezePanes.freezeRows(1);
sheet.tables.add(`A1:F${usedRows}`, true, "FightEventsCompactTable");

const errors = await workbook.inspect({
  kind: "match",
  searchTerm: "#REF!|#DIV/0!|#VALUE!|#NAME\\?|#N/A",
  options: { useRegex: true, maxResults: 100 },
  summary: "formula error scan",
});
console.log(errors.ndjson);

await fs.mkdir(outputDir, { recursive: true });
const xlsx = await SpreadsheetFile.exportXlsx(workbook);
await xlsx.save(outputPath);

console.log(JSON.stringify({ outputPath, rows: events.length }, null, 2));
