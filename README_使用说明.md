# Dota2 战术事件项目 v1.9 完整包

生成日期：2026-06-10

## 直接使用

- `final_excels/`：五局最终战术事件表，文件名为 `战术事件_matchid.xlsx`。
- `definitions/`：v1.9 战术事件定义文件。
- `algorithm_rules/`：v1.9 识别算法纯数据 Excel，适合人工修改规则和阈值。
- `outputs/`：保留项目原始输出结构，包含 v1.9 Excel、定义、算法表、每局 JSON/CSV/MD/合并映射。
- `work/`：v1.8/v1.9 后处理、诊断、校验、导出脚本。

## 重要说明

1. 包内不包含带数据库连接信息的原始抓取脚本，只包含 v1.9 交付、后处理和导出相关文件。
2. 若只查看或人工修改结果，直接打开 `final_excels/`、`definitions/`、`algorithm_rules/` 即可。
3. 若要在另一台电脑复跑脚本，请保持本包目录结构不变；Python 后处理脚本默认读取 `outputs/tactical_events_matchid/`，Node 导出脚本默认读取 `outputs/`。
4. Excel 导出脚本依赖 Node.js 及 `@oai/artifact-tool`；Python 校验/后处理依赖 Python 3。

## v1.9 文件要点

- 最终标签中不再包含“抓人”。
- 新增/保留 `肉山击杀` 独立标签。
- 包含 v1.9 核心修复：有击杀父事件吸收相邻/重叠无独立击杀短窗口。
- 包含控符去重、未刷新侧控符、肉山击杀/掉落合并、相邻战斗去重相关数据和脚本。
