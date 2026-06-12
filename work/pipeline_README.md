# 数据库复算管线草案

这组脚本用于把 StarRocks 里的比赛原始/半原始表抽成本地文件，再从本地文件计算可追溯的战术事件。

## 1. 设置连接环境变量

不要把密码写进脚本。每次运行前在 PowerShell 设置：

```powershell
$env:DB_HOST='47.86.96.51'
$env:DB_PORT='9030'
$env:DB_USER='dota2_reader'
$env:DB_PASS='******'
```

可选：

```powershell
$env:DB_NAME='dota2_analysis'
```

## 2. 抽取单局数据

```powershell
python work/pipeline_extract_match.py 8826099852 --dt 2026-05-26
```

默认输出到：

```text
raw_db/match_8826099852/
```

其中每张表一个 `jsonl` 文件，另有 `manifest.json` 记录行数、分区和路径。

默认抽取核心表：

- `match_info`
- `players`
- `match_picks_bans`
- `player_intervals2`
- `combat_logs`
- `match_chat_events`
- `tower_status_update`
- `hero_status_update`
- `other_unit_sync`
- `dota_model_neutral_siege_creep`
- `hero_roshan_miniboss_vtord`
- `ward_placed_left_fact`

如果需要操作/点击数据：

```powershell
python work/pipeline_extract_match.py 8826099852 --dt 2026-05-26 --include-actions
```

## 3. 计算首批事实类事件

```powershell
python work/pipeline_compute_fact_events.py raw_db/match_8826099852
```

默认输出到：

```text
computed_events/fact_events_8826099852.json
computed_events/fact_events_8826099852.csv
```

当前覆盖的事实类/较高确定性事件：

- `对线分路`
- `囤野/堆野`
- `控符`
- `开雾`
- `肉山击杀`
- `资源获得`
- `带盾阵亡`

`对线分路` 规则：

- 读取 `player_intervals2` 里每个英雄 `0 <= time <= 300` 的 `x/y`。
- 原始坐标路线判断：`y - x > 35` 为上路 top，`x - y > 35` 为下路 bot，其他为中路 mid。
- 每个英雄统计 top/mid/bot 快照次数，次数最多为主分路。
- `lane_seconds = count * (300 / 总有效样本数)`。
- 非主路线停留时间 `>= 60s` 记录为“游走/次要路”。
- 阵营映射：天辉 bot=优势路、top=劣势路、mid=中路；夜魇 top=优势路、bot=劣势路、mid=中路。
- 每个英雄输出一条事件，时间范围固定 `00:00-05:00`，置信度暂写 `未知`。

`对线区` 规则：

- `对线区` 是 `断线` 和 `勾兵` 共用的全局区域口径，详见 `work/lane_zone_definition.md`。
- 每一路单独生成 `lane_zone[top/mid/bot]`。
- 样本来源：前 10 分钟 `combat_logs` 里的线上小兵死亡点。
- 正常交汇样本条件：
  - `type = DOTA_COMBATLOG_DEATH`。
  - 死亡目标是线上小兵，且能识别阵营和路线。
  - 该路线双方一塔都存活。
  - 死亡点 `500` 范围内存在敌方同路线线上小兵。
- 区域生成：
  - 每一路取正常交汇样本点的 `x/y` 分布。
  - 去掉极端值：默认使用 `10%` 到 `90%` 分位。
  - 向外扩 `10 raw` 坐标作为容差。
  - 样本数至少 `12` 个才启用本局自适应区域。
- 兜底：
  - 本局样本足够，使用本局 `lane_zone`。
  - 本局样本不足，使用多局样本生成的 `global_lane_zone`。
  - 两者都缺失时，不自动确认 `断线/勾兵`，只输出弱候选或跳过。

`断线` 候选规则：

- 数据来源：`combat_logs` 小兵死亡事件、`tower_status_update` 防御塔状态、`dota_model_neutral_siege_creep` 线上小兵状态。
- 只看前 10 分钟：`0 <= time <= 600`。
- 单条候选证据：
  - `type = DOTA_COMBATLOG_DEATH`。
  - 死亡目标是某阵营某一路线上小兵。
  - 该阵营该路线一塔在死亡时仍存活。
  - 死亡位置位于该路线 `lane_zone` 外。
  - 死亡小兵 `500` 范围内没有敌方线上小兵。
  - 击杀来源能映射为敌方英雄。
- 聚合输出：
  - 按 `(击杀英雄, 被断兵阵营, 路线)` 聚合。
  - 同一英雄、同一路线、`10` 秒内连续触发 `>= 2` 或 `>= 3` 个符合断线规则的小兵死亡，才输出一条 `断线` 事件。
  - `>= 2` 偏召回，`>= 3` 偏精确；最终阈值用人工批准样本做假设检验后固定。
  - 事件时间范围取该组小兵死亡的首尾时间，结果字段写明断兵英雄、被断兵阵营、路线、小兵数量和一塔存活证据。
- 若缺少活体线上小兵位置快照，不能稳定判断 `500` 范围内是否有敌方小兵，只能输出弱候选或暂不自动输出。

`勾兵` 候选规则：

- 数据来源：`combat_logs` 小兵死亡/经验事件、`tower_status_update` 防御塔状态、`dota_model_neutral_siege_creep` 线上小兵状态。
- 只看前期对线阶段，默认先用 `0 <= time <= 600`，后续可用人工样本校准。
- 以天辉小兵为例：
  - 识别死亡目标是天辉某一路线上小兵。
  - 天辉该路线一塔在死亡时仍存活。
  - 死亡位置位于该路线 `lane_zone` 外。
  - 死亡天辉小兵 `500` 范围内存在夜魇线上小兵。
  - 若夜魇至少有一名英雄获得了该天辉小兵死亡产生的经验，则记为夜魇对天辉该路线的 `勾兵` 事件。
- 反向同理：夜魇某路线小兵在夜魇该路线一塔存活时，死于对线区外，`500` 范围内有天辉线上小兵；若天辉英雄获得经验，则记为天辉 `勾兵` 事件。
- 聚合输出：
  - 按 `(获得经验阵营, 被勾兵阵营, 路线)` 聚合。
  - 同一路线连续小兵死亡可合并为一条事件，默认用 `10` 秒窗口；窗口内死亡小兵越多，证据越强。
  - `heroes` 输出获得经验的敌方英雄；若多人获得经验，则全部列入。
  - 结果字段以经验获得为准，写明“某阵营英雄获得某阵营某路线小兵死亡经验”，并列出获得经验英雄；可附带死亡小兵数量和附近敌方小兵证据。
- 与 `断线` 的互斥：
  - 同一个小兵死亡，若 `500` 范围内无敌方小兵，进入 `断线` 证据。
  - 若 `500` 范围内有敌方小兵，且敌方英雄获得经验，输出 `勾兵`，结果记为经验获得英雄。
- 若缺少小兵位置或经验获得明细，只能输出弱候选；不能直接确认为 `勾兵`，也不能填写确定结果。

## 4. 下一步

## 4. 计算战斗聚类

```powershell
python work/pipeline_compute_fight_records.py raw_db/match_8826099852
```

默认输出到：

```text
computed_events/fight_records_8826099852.json
computed_events/fight_events_8826099852.json
computed_events/fight_events_8826099852.csv
```

`fight_records` 是偏底层的审计文件，包含时间窗、中心坐标、双方英雄、伤害、死亡、信号数量和证据。`fight_events` 是初版战术事件行，目前只输出：

- `GANK`
- `小规模冲突`
- `团战`

`GANK` 规则：

- 在战斗聚类结果中优先判断。
- 若一方直接参与英雄数 `>= 2`，另一方参与英雄数 `<= 1`，则输出 `GANK`。
- 直接参与英雄指造成伤害或施加控制的英雄；单纯被打、被控、死亡不算直接参与。
- `GANK` 优先级高于 `小规模冲突`，低于后续特殊上下文标签。

当前战斗聚类仍是保守草案：它优先保留可解释证据，尚未完整接入对线消耗过滤、特殊战斗上下文、相邻战斗合并和人工复核规则。

## 5. 导出案例同款 Excel

```powershell
node work/pipeline_build_tactical_table.mjs 8826099852
```

默认读取：

```text
computed_events/fact_events_8826099852.json
computed_events/fight_events_8826099852.json
definitions/dota2_tactical_event_definitions_v1.9_20260609.xlsx
```

默认输出：

```text
pipeline_excels/战术事件_8826099852_pipeline.xlsx
pipeline_excels/战术事件_8826099852_pipeline_preview.png
```

Excel 结构与交付案例一致：

- `战术事件`
- `标签计数`
- `定义版本`

主表列固定为：

```text
id, match_id, labels, time_range, heroes, 结果, 批注
```

`confidence` 不在最终事件表中输出。置信度作为口径模型质量指标，见 `work/hypothesis_testing_confidence.md`。

## 6. 下一步

后续应在同一输入结构上继续补：

- 参战过滤：用 `player_intervals2` 的位置和 `combat_logs` 的交互排除远端无关英雄。
- 特殊上下文：把 `tower_status_update`、`other_unit_sync`、`hero_roshan_miniboss_vtord` 接入高地团、守塔团、肉山团、魔晶团。
- 对线互斥：过滤纯对线消耗，避免把持续换血误输出为战术冲突。
- 合并去重：复用 v1.8/v1.9 的相邻近距战斗和有击杀父事件吸收短窗口规则。
- 人工复核证据：每条事件保留 `evidence`，用于解释为什么输出该事件。
