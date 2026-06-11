# 对线区定义口径

## 目标

对线区用于判断小兵死亡是否发生在正常兵线交汇区域内。

`断线` 和 `勾兵` 中的“对线区外”统一引用本口径，不再各自写死坐标。

## 核心定义

对线区 = 某一路在一塔存活阶段，双方线上小兵正常交汇、交战、死亡的主要区域。

对于 `top/mid/bot` 每一路，都生成一个独立区域：

```text
lane_zone[top]
lane_zone[mid]
lane_zone[bot]
```

如果某个该路线小兵死亡点落在对应 `lane_zone` 内，视为正常对线区内。
如果死亡点落在对应 `lane_zone` 外，才进入 `断线` 或 `勾兵` 的候选判断。

## 生成样本

每局比赛先用本局数据生成对线区样本。

候选样本来自 `combat_logs` 小兵死亡事件：

```text
0 <= time <= 600
type = DOTA_COMBATLOG_DEATH
targetname 是线上小兵
能够识别该小兵所属阵营和路线
该路线双方一塔都存活
死亡点 500 范围内存在敌方同路线线上小兵
```

这些样本表示“正常兵线交汇后产生的小兵死亡”。

样本按路线聚合，不按阵营拆分：

```text
top_samples = top 路双方小兵的正常交汇死亡点
mid_samples = mid 路双方小兵的正常交汇死亡点
bot_samples = bot 路双方小兵的正常交汇死亡点
```

## 区域生成

每一路用样本点生成一个稳健矩形区域。

默认算法：

```text
x_min = x 的 10% 分位数
x_max = x 的 90% 分位数
y_min = y 的 10% 分位数
y_max = y 的 90% 分位数

lane_zone = [x_min - margin, x_max + margin] × [y_min - margin, y_max + margin]
```

默认参数：

```text
lane_zone_sample_window = 0-600 秒
lane_zone_near_enemy_creep_radius = 500 码
lane_zone_quantile_low = 0.10
lane_zone_quantile_high = 0.90
lane_zone_margin_raw = 10 raw 坐标
lane_zone_min_samples = 12
```

如果某一路有效样本数少于 `lane_zone_min_samples`，该局不使用本局自适应区域，改用全局基准区域。

## 全局基准区域

全局基准区域从多局人工批准样本或历史正常对线样本中生成。

生成方式与本局自适应区域一致：

```text
按路线汇总多局正常交汇死亡点
去极值分位数
外扩 margin
得到 global_lane_zone[top/mid/bot]
```

使用优先级：

```text
本局 lane_zone 样本充足 => 使用本局自适应区域
本局样本不足 => 使用 global_lane_zone
两者都缺失 => 不自动确认断线/勾兵，只输出弱候选或跳过
```

## 判断函数

给定某个小兵死亡点 `(x, y)` 和该小兵所属路线 `lane`：

```text
if x_min <= x <= x_max and y_min <= y <= y_max:
    in_lane_zone = true
else:
    in_lane_zone = false
```

`断线` 和 `勾兵` 只在 `in_lane_zone = false` 时继续判断。

## 与断线、勾兵的关系

断线：

```text
己方某路线小兵死在 lane_zone 外
且该路线己方一塔存活
且 500 范围内无敌方线上小兵
且击杀来源为敌方英雄
```

勾兵：

```text
己方某路线小兵死在 lane_zone 外
且该路线己方一塔存活
且 500 范围内有敌方线上小兵
且敌方英雄获得该小兵死亡经验
```

## 审计输出

每次生成 `断线` 或 `勾兵` 候选时，证据中必须保留：

```text
lane
death_time
death_x/death_y
lane_zone_source: match_adaptive 或 global_baseline
lane_zone_bbox
in_lane_zone=false
near_enemy_creep_count_500
tower_alive=true
```

这样人工复核时可以直接判断“对线区外”是否合理。
