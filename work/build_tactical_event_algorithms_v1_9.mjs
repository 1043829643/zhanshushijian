import fs from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { SpreadsheetFile, Workbook } from "@oai/artifact-tool";

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const outputDir = path.join(root, "outputs");
const outputPath = path.join(outputDir, "dota2_tactical_event_recognition_algorithms_v1.9_20260609.xlsx");
const previewDir = path.join(outputDir, "algorithm_v1.9_previews");

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

function writeSheet(workbook, name, headers, rows, widths, bodyRowHeightPx = 54) {
  const sheet = workbook.worksheets.add(name);
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
  header.format.rowHeightPx = 34;

  if (rows.length > 0) {
    const body = sheet.getRangeByIndexes(1, 0, rows.length, headers.length);
    body.format.rowHeightPx = bodyRowHeightPx;
  }

  for (let i = 0; i < widths.length; i += 1) {
    sheet.getRange(`${colLetter(i)}:${colLetter(i)}`).format.columnWidthPx = widths[i];
  }
  sheet.freezePanes.freezeRows(1);
  sheet.showGridLines = false;
  return sheet;
}

const versionRows = [
  ["版本", "v1.9", "当前五局战术事件表所用规则", ""],
  ["生成日期", "2026-06-09", "按用户要求输出为纯数据 Excel，便于人工修改", ""],
  ["适用范围", "Dota2 战术事件自动识别、后处理、Excel 输出", "包括标签、阈值、数据源、流程、合并去重和输出格式", ""],
  ["输出标签数", 19, "对线分路、勾兵、断线、拉野、囤野/堆野、控符、蹲人、开雾、GANK、小规模冲突、团战、肉山团、肉山击杀、高地团、守塔团、肉山尝试、魔晶团、资源获得、带盾阵亡", ""],
  ["已删除标签", "抓人", "不作为最终输出标签；历史候选会重映射/合并为GANK、小规模冲突或团战", ""],
  ["v1.9核心变更", "有击杀父事件吸收相邻/重叠无独立击杀短窗口", "修复 BKB、TP、短控制、短伤害等微事件从同一战斗中被拆成独立行的问题", ""],
  ["表格用途", "人工修改识别算法", "建议优先改“参数阈值”和“人工修改入口”，再同步到脚本", ""],
  ["最终事件表列", "id, match_id, labels, confidence, time_range, heroes, 结果, 批注", "批注列为空白人工列；所有单元格有框线", ""],
  ["注意", "本文件是算法数据化说明，不直接驱动当前脚本执行", "人工修改后可作为下一版脚本实现依据", ""],
];

const labelRows = [
  ["对线分路", "auto", "A", "player_intervals2; match_picks_bans/players", "0-5分钟位置快照；英雄在优/中/劣三路停留时间最长的路线为主分路；次要路停留>60秒记录游走/次要路", "00:00-05:00", "路线区域判定；次要路阈值60秒", "输出单个英雄及阵营；不涉及敌方参与", "结果=分路结果：主分路 + 次要路说明", "每名英雄一行；按时间排序", "基础事实类，优先级低于战斗/资源类", "换线、游走英雄需要人工复核", "v1.4-v1.9", ""],
  ["勾兵", "candidate_review", "B", "combat_logs; tower_status_update; dota_model_neutral_siege_creep; derived: lane_zone", "某阵营某一路线上小兵死亡；该阵营该路线一塔仍存活；死亡位置在该路线lane_zone外；死亡小兵500范围内存在敌方线上小兵；若敌方英雄获得该小兵死亡经验，则输出勾兵", "通常00:00-10:00", "lane_zone外；附近敌方小兵半径500码；连续小兵死亡默认10秒窗口合并", "获得经验的敌方英雄；多人获得经验则全部列入", "结果=获得经验英雄获得被勾兵阵营某路线小兵死亡经验；可附死亡小兵数量和附近敌方小兵证据", "单个小兵死亡作为证据；按获得经验阵营+被勾兵阵营+路线聚合，连续窗口合并输出", "与断线互斥：附近无敌方小兵为断线证据，附近有敌方小兵且敌方英雄获经验则输出勾兵", "需要lane_zone、活体线上小兵位置和经验获得明细；缺失时只能弱候选，不能填写确定结果", "v1.10草案", ""],
  ["断线", "candidate_review", "B", "combat_logs; tower_status_update; dota_model_neutral_siege_creep; derived: lane_zone", "10分钟前某阵营某一路线上小兵死亡；该阵营该路线一塔仍存活；死亡位置在该路线lane_zone外；死亡小兵500范围内无敌方线上小兵；击杀来源可映射为敌方英雄", "通常00:00-10:00", "lane_zone外；附近敌方小兵半径500码；同一英雄同一路线10秒内连续触发>=2或>=3个小兵死亡后输出", "执行断兵的敌方英雄", "结果=断兵英雄、被断兵阵营、路线、小兵数量、一塔存活证据", "单个小兵死亡只作为证据；按击杀英雄+被断兵阵营+路线聚合，连续10秒窗口达到阈值才输出事件", "与拉野、正常兵线交汇清线互斥；一塔已掉后不判断线", "需要lane_zone和活体线上小兵位置；若缺少该数据，只能输出弱候选或暂不自动输出", "v1.10草案", ""],
  ["拉野", "candidate_review", "B", "combat_logs", "15分钟前线上小兵与中立单位发生连续伤害/死亡交互；中路排除；同组位置相近且时间间隔<=35秒", "00:00-15:00", "拉野组间隔35秒；位置距离28 raw；附近英雄1500码", "附近双方/己方英雄，去重输出", "结果=线上小兵与中立单位交互样例", "死亡<2且交互<3则过滤", "与断线、普通野区交互需复核", "英雄拉仇恨起点仍需人工核查", "v1.4-v1.9", ""],
  ["囤野/堆野", "candidate_review", "B", "player_intervals2", "camps_stacked 或 creeps_stacked 计数较上一快照增加", "计数增加时刻±5秒", "计数增量>0", "执行英雄及阵营", "结果默认留空或写 camps/creeps 增量", "同一快照只输出一次", "事实较可靠但成功语义需核查", "数据库计数可定位堆野，但不能解释完整动作过程", "v1.4-v1.9", ""],
  ["控符", "auto", "A/B", "match_chat_events; player_intervals2", "RUNE_PICKUP/RUNE_BOTTLE/RUNE_DENY；智慧/赏金/水符/护盾/能量符统一为控符；能量符未刷新侧若有英雄也记录控符", "结果事件t-5到t；智慧符t-10到t；未刷新侧spawn_time-5到spawn_time", "符点附近1000码；能量符刷新间隔120秒；罐装后90秒内同英雄同神符拾取去重", "heroes=某符点附近两方英雄；未刷新侧也按该侧符点附近英雄记录", "结果=英雄名+拾取/罐装/反补+神符；未刷新侧=神符刷新在另一侧；无结果可留空", "每颗神符只记录一次；赏金区分天辉赏金/夜魇赏金；罐装优先于后续拾取", "控符统一标签，不再输出帮控符/控智慧神符", "符点归属和附近英雄可人工复核", "v1.5-v1.9", ""],
  ["蹲人", "candidate_review", "B", "英雄位置; 视野/路径上下文", "英雄在潜在目标500-1000码范围长期隐藏/等待，通常在树林、阴影、视野盲区", "等待开始-接触或离开", "建议500-1000码；等待时长需人工设定", "蹲守方与被蹲目标", "结果可留空或写蹲守结果", "当前建议人工核查，不强制自动输出", "若后续造成战斗，优先战斗类标签", "语义强依赖录像和视野", "v1.4-v1.9", ""],
  ["开雾", "candidate_review", "B", "combat_logs", "item_smoke_of_deceit 使用；结合 smoke modifier 添加/移除确定进入雾英雄和结束时间", "使用时刻到第一个相关modifier移除；默认最短t+8，最长t+120或下一次同施法者开雾前", "进入英雄1000码兜底；modifier linked window 120秒", "使用者+进入雾的英雄", "结果=使用者、进入英雄、位置", "按使用时间+队伍+英雄集合去重", "可与后续战斗并存", "DB可确定用雾，但开雾意图需复核", "v1.4-v1.9", ""],
  ["GANK", "candidate_review", "B", "combat_logs; player_intervals2", "战斗聚类中一方N抓1：一方参与英雄>=2，另一方参与英雄<=1", "聚类开始-结束", "继承战斗聚类时间/空间阈值；参与英雄=进入同一战斗簇的攻击者、受击者、被控制者或死亡者", "gank方英雄 + 被抓英雄", "结果=击杀比分；死亡列表；无死亡写未记录英雄击杀/死亡无", "优先于小规模冲突判定；后续可被守塔团/高地团/肉山团等特殊上下文修饰", "低于团战特殊上下文，高于小规模冲突", "需要人工校准N抓1但未实质开战、远程消耗等边界", "v1.10草案", ""],
  ["小规模冲突", "candidate_review", "B", "combat_logs; match_chat_events; player_intervals2", "双方局部伤害/控制/死亡聚类；通常双方各2人以上且总人数<=7，或总人数>=4且有死亡但未达团战规模", "聚类开始-结束；候选先手可提前到控制/大伤害/先手技能时刻", "10秒桶；raw聚类38；跨桶间隔12秒；聚类距离48 raw；细化事件前后6秒、中心62 raw；英雄参与默认1600码", "只保留战斗核心附近或对参与英雄造成伤害/控制的英雄；排除远处未命中核心的独立消耗", "结果=击杀比分；死亡列表；无死亡写未记录英雄击杀/死亡无", "v1.7+吸收旧单点候选；v1.8/v1.9继续合并重复短窗口", "低于团战，高于纯资源/控符事实", "边界和语义建议录像复核", "v1.4-v1.9", ""],
  ["团战", "candidate_review", "B", "combat_logs; match_chat_events; player_intervals2", "双方多人持续交战；双方总参与通常各4人以上，且直接参与各>=3人，并满足总伤害>=2500、死亡>=2或持续>=15秒之一", "聚类开始-结束；先手技能可提前开始", "团战人数阈值4v4；直接参与3v3；伤害2500；死亡2；持续15秒", "同小规模冲突，按空间/交互排除远端独立交互", "结果=击杀比分；死亡列表；带盾阵亡需标注", "与特殊战斗标签可并存：肉山团/高地团/守塔团/魔晶团", "优先级高于小规模冲突；被特殊战斗标签修饰", "远距离同时开战要拆分，不可只按时间合并", "v1.4-v1.9", ""],
  ["肉山团", "candidate_review", "B", "combat_logs; match_chat_events; player_intervals2", "肉山仍存活，且战斗围绕肉山坑/肉山伤害/肉山击杀上下文展开", "战斗窗口；肉山伤害前后30秒；肉山击杀前45秒到后75秒", "肉山坑2000码；肉山死亡后480秒内视为未刷新", "参与战斗双方英雄", "结果追加目标：肉山相关；肉山击杀结果由肉山击杀标签独立记录", "肉山死亡后的追击不再因靠近肉山坑误标肉山团", "可与团战/小规模冲突并存", "需区分肉山尝试、肉山击杀、肉山后追击", "v1.4-v1.9", ""],
  ["肉山击杀", "auto", "A", "combat_logs; match_chat_events", "Roshan死亡事件；统一记录为肉山击杀，不再作为普通资源获得输出；合并同一波肉山掉落", "击杀时刻到最后一个20秒内相关掉落", "掉落合并窗口20秒；击杀归属优先combat_logs roshan_death；缺失时用5秒内掉落阵营兜底", "获得掉落的英雄/阵营；heroes合并击杀和掉落相关英雄", "结果=哪方击杀肉山；哪个英雄获得盾/奶酪/刷新碎片/战旗等", "CHAT_MESSAGE_ROSHAN_KILL的player/value不作为击杀英雄依据", "优先级高于资源获得；同一肉山只一行", "需人工检查掉落事件是否完整合并", "v1.8-v1.9", ""],
  ["高地团", "candidate_review", "B", "combat_logs; tower/barracks events; player_intervals2", "一方或双方在高地、基地入口、破路区域发生团战/小规模冲突", "战斗窗口；兵营击杀前20秒到后60秒", "中心raw坐标x<95且y<95或x>158且y>158；或兵营事件附近", "参与战斗双方英雄", "结果继承战斗结果", "作为附加标签插入战斗标签前", "特殊战斗标签优先级高", "高地前poke与正式开团需复核", "v1.4-v1.9", ""],
  ["守塔团", "candidate_review", "B", "tower_status_update; combat_logs", "一方在防御塔附近约1000码内防守时发生的战斗", "战斗开始前5秒到结束后5秒内塔上下文", "防御塔1000码；塔hp>0", "参与战斗双方英雄", "结果继承战斗结果并附塔上下文", "作为附加标签追加，可与小规模冲突/团战并存", "特殊上下文标签", "塔已掉或追击阶段不应误标", "v1.4-v1.9", ""],
  ["肉山尝试", "candidate_review", "B", "combat_logs", "对Roshan造成伤害但未完成击杀，也未转化为肉山团", "连续Roshan伤害组；组内间隔<=45秒；结束后+10秒", "总伤害>=1500；攻击英雄>=1；组内无击杀且结束后8秒无击杀", "对肉山造成伤害的英雄", "结果=Roshan总伤害和未击杀说明", "若双方围绕肉山开战，优先肉山团", "低于肉山团/肉山击杀", "偷Roshan试探与真尝试需复核", "v1.4-v1.9", ""],
  ["魔晶团", "candidate_review", "B", "combat_logs; match_chat_events; player_intervals2", "折磨者/魔晶仍存活时，围绕魔晶区域发生团战或资源争夺", "战斗窗口；魔晶伤害前后30秒；击杀前45秒到后60秒", "魔晶区域1500-2000码；20分钟后可存活；击杀后600秒内视为未刷新", "参与战斗双方英雄", "结果继承战斗结果或写魔晶归属", "魔晶击杀后追击不再误标魔晶团", "特殊战斗标签优先级高", "需区分单方打魔晶与双方争夺", "v1.4-v1.9", ""],
  ["资源获得", "auto", "A", "match_chat_events; combat_logs", "独立记录关键资源事实：魔晶/折磨者、战旗等；v1.8后肉山击杀与肉山掉落并入肉山击杀，不再单独输出普通资源获得", "资源事件时刻", "Roshan kill归属兜底5秒；普通资源按chat事件", "获得资源的英雄或阵营", "结果=英雄/阵营+资源动作", "旧抢盾/抢奶/抢刷新并入资源获得或肉山击杀口径", "低于肉山击杀", "需要确认非肉山资源未被误合并", "v1.5-v1.9", ""],
  ["带盾阵亡", "auto", "A/B", "match_chat_events; combat_logs", "持有不朽盾的英雄死亡/消耗盾时独立记录；战斗死亡列表也标注[带盾阵亡]", "死亡时刻", "Aegis领取后300秒内的同英雄死亡；每个盾只消费一次", "带盾阵亡英雄及阵营", "结果=死亡时间、英雄、Aegis获取时间", "可与团战/小规模冲突并存", "资源消耗事实类", "复活/盾消耗边界建议复核", "v1.5-v1.9", ""],
];

const parameterRows = [
  ["lane_initial_window", "0-300", "秒", "对线分路", "初始分路线判断窗口", "v1.4", "是", ""],
  ["secondary_lane_seconds", "60", "秒", "对线分路", "超过该停留时长记录游走/次要路", "v1.4", "是", ""],
  ["lane_zone_sample_window", "0-600", "秒", "对线区", "生成本局对线区的正常交汇样本窗口", "v1.10草案", "是", ""],
  ["lane_zone_near_enemy_creep_radius", "500", "码", "对线区", "正常交汇样本要求死亡点附近存在敌方同路线线上小兵", "v1.10草案", "是", ""],
  ["lane_zone_quantile_low", "0.10", "比例", "对线区", "生成对线区bbox时x/y低分位，去除极端死亡点", "v1.10草案", "是", ""],
  ["lane_zone_quantile_high", "0.90", "比例", "对线区", "生成对线区bbox时x/y高分位，去除极端死亡点", "v1.10草案", "是", ""],
  ["lane_zone_margin_raw", "10", "raw坐标", "对线区", "对线区bbox外扩容差", "v1.10草案", "是", ""],
  ["lane_zone_min_samples", "12", "个", "对线区", "单路线本局自适应对线区所需最少正常交汇样本", "v1.10草案", "是", ""],
  ["stack_event_window", "±5", "秒", "囤野/堆野", "堆野计数增加事件输出窗口", "v1.4", "是", ""],
  ["pull_lane_max_time", "900", "秒", "拉野", "拉野候选只看前15分钟", "v1.4", "是", ""],
  ["pull_group_gap", "35", "秒", "拉野", "线上兵与中立交互合并为同一拉野组的最大间隔", "v1.4", "是", ""],
  ["pull_group_distance_raw", "28", "raw坐标", "拉野", "拉野组位置合并距离", "v1.4", "是", ""],
  ["pull_nearby_hero_radius", "1500", "码", "拉野", "拉野附近英雄归属半径", "v1.4", "是", ""],
  ["aggro_lane_max_time", "600", "秒", "勾兵", "勾兵候选默认只看前10分钟，可由人工样本校准", "v1.10草案", "是", ""],
  ["aggro_lane_enemy_creep_radius", "500", "码", "勾兵", "死亡小兵附近存在敌方线上小兵的判定半径", "v1.10草案", "是", ""],
  ["aggro_lane_group_gap", "10", "秒", "勾兵", "同一路线连续勾兵证据合并窗口", "v1.10草案", "是", ""],
  ["cut_lane_max_time", "600", "秒", "断线", "断线候选只看前10分钟", "v1.10草案", "是", ""],
  ["cut_lane_enemy_creep_radius", "500", "码", "断线", "死亡小兵附近无敌方线上小兵的判定半径", "v1.10草案", "是", ""],
  ["cut_lane_group_gap", "10", "秒", "断线", "同一英雄同一路线连续断兵证据合并窗口", "v1.10草案", "是", ""],
  ["cut_lane_min_creep_deaths", "2或3", "个", "断线", "10秒窗口内符合断线单条证据的小兵死亡数量阈值；2偏召回，3偏精确，需假设检验确定", "v1.10草案", "是", ""],
  ["rune_near_radius", "1000", "码", "控符", "符点附近两方英雄识别半径", "v1.5", "是", ""],
  ["power_rune_spawn_interval", "120", "秒", "控符", "河道能量符刷新间隔，用于未刷新侧控符", "v1.5", "是", ""],
  ["power_rune_min_spawn_time", "360", "秒", "控符", "能量符最早按6分钟刷新计算", "v1.5", "是", ""],
  ["power_rune_spawn_match_tolerance", "90", "秒", "控符", "将结果事件反推到最近能量符刷新点的容差", "v1.5", "是", ""],
  ["rune_bottle_pickup_suppression", "90", "秒", "控符", "同英雄同神符罐装后拾取日志去重窗口", "v1.5", "是", ""],
  ["wisdom_rune_window", "10", "秒", "控符", "智慧符事件起点=t-10", "v1.5", "是", ""],
  ["smoke_link_window", "120", "秒", "开雾", "开雾使用与modifier关联最大窗口", "v1.4", "是", ""],
  ["smoke_modifier_near_window", "3", "秒", "开雾", "无施法者关联时按同队modifier时间兜底", "v1.4", "是", ""],
  ["smoke_fallback_near_radius", "1000", "码", "开雾", "无modifier时按使用者附近队友兜底", "v1.4", "是", ""],
  ["combat_damage_min_event", "45", "伤害", "小规模冲突/团战", "进入战斗检测的单条伤害最小值", "v1.4", "是", ""],
  ["combat_seed_damage", "550", "伤害", "小规模冲突/团战", "初始空间簇成为战斗候选的伤害阈值，死亡可替代", "v1.4", "是", ""],
  ["combat_refined_damage", "900", "伤害", "小规模冲突/团战", "细化后无死亡战斗的最低总伤害", "v1.4", "是", ""],
  ["combat_bucket", "10", "秒", "小规模冲突/团战", "战斗检测分桶", "v1.4", "是", ""],
  ["combat_cluster_distance_raw", "38", "raw坐标", "小规模冲突/团战", "同10秒桶内空间聚类距离", "v1.4", "是", ""],
  ["combat_cluster_merge_gap", "12", "秒", "小规模冲突/团战", "相邻空间簇合并最大时间间隔", "v1.4", "是", ""],
  ["combat_cluster_merge_distance_raw", "48", "raw坐标", "小规模冲突/团战", "相邻空间簇合并最大中心距离", "v1.4", "是", ""],
  ["combat_refine_time_pad", "6", "秒", "小规模冲突/团战", "细化收集伤害/控制/死亡的前后时间", "v1.4", "是", ""],
  ["combat_refine_distance_raw", "62", "raw坐标", "小规模冲突/团战", "细化事件距离中心最大距离", "v1.4", "是", ""],
  ["combat_participant_radius", "1600", "码", "小规模冲突/团战", "英雄参与战斗核心的默认最大距离", "v1.5", "是", ""],
  ["combat_midlane_exclusion_distance", "1000", "码", "小规模冲突/团战", "边路冲突中心时，中路线英雄超过该距离且未命中核心目标则排除", "v1.5", "是", "本质是远端未交互排除，不限中路或10分钟前"],
  ["combat_nearby_teammate_radius", "1000", "码", "小规模冲突/团战", "细化阶段把近处同队英雄补入参战者", "v1.4", "是", ""],
  ["teamfight_min_side_count", "4", "人", "团战", "双方总参与各至少4人", "v1.4", "是", ""],
  ["teamfight_min_direct_count", "3", "人", "团战", "双方直接参与各至少3人", "v1.4", "是", ""],
  ["teamfight_damage_threshold", "2500", "伤害", "团战", "达团战规模时的伤害阈值", "v1.4", "是", ""],
  ["teamfight_death_threshold", "2", "死亡", "团战", "达团战规模时死亡数阈值", "v1.4", "是", ""],
  ["teamfight_duration_threshold", "15", "秒", "团战", "达团战规模时持续时长阈值", "v1.4", "是", ""],
  ["gank_min_attacker_side_heroes", "2", "人", "GANK", "GANK方至少有2名参与英雄，参与英雄指进入同一战斗簇的攻击者、受击者、被控制者或死亡者", "v1.10草案", "是", ""],
  ["gank_max_victim_side_heroes", "1", "人", "GANK", "被抓一方参与英雄最多1人，即N抓1", "v1.10草案", "是", ""],
  ["skirmish_max_total_heroes", "7", "人", "小规模冲突", "双方至少2v2且总人数不超过7时归为小规模冲突", "v1.4", "是", ""],
  ["fight_dedup_gap_initial", "8", "秒", "小规模冲突/团战", "初始fight record近邻合并时间", "v1.4", "是", ""],
  ["fight_dedup_distance_raw", "58", "raw坐标", "小规模冲突/团战", "初始fight record近邻合并距离", "v1.4", "是", ""],
  ["fight_overlap_shared_heroes", "3", "人", "小规模冲突/团战", "重叠fight record合并的共享英雄数", "v1.4", "是", ""],
  ["pickoff_enemy_damage_ratio", "0.80", "比例", "历史抓人候选", "旧单点击杀候选生成条件；最终不输出抓人标签", "v1.6", "是", ""],
  ["pickoff_ally_near_limit", "1", "人", "历史抓人候选", "旧单点击杀候选受害方附近队友上限", "v1.6", "是", ""],
  ["escape_pickoff_window", "18", "秒", "历史抓人候选", "BKB/TP逃脱前受伤/受控窗口；最终需重映射或合并", "v1.6", "是", ""],
  ["escape_pickoff_damage_min", "180", "伤害", "历史抓人候选", "无控制时逃脱候选最低敌方伤害", "v1.6", "是", ""],
  ["roshan_context_radius", "2000", "码", "肉山团", "肉山团上下文范围", "v1.4", "是", ""],
  ["roshan_context_min_time", "600", "秒", "肉山团", "10分钟后靠近肉山坑可触发肉山上下文", "v1.4", "是", ""],
  ["roshan_damage_context_pad", "30", "秒", "肉山团", "肉山伤害与战斗窗口前后关联时间", "v1.4", "是", ""],
  ["roshan_kill_context_before", "45", "秒", "肉山团", "肉山击杀与战斗关联前置时间", "v1.4", "是", ""],
  ["roshan_kill_context_after", "75", "秒", "肉山团", "肉山击杀与战斗关联后置时间", "v1.4", "是", ""],
  ["roshan_respawn_suppression", "480", "秒", "肉山团", "肉山死亡后该时间内不视为存活", "v1.4", "是", ""],
  ["roshan_attempt_group_gap", "45", "秒", "肉山尝试", "Roshan伤害组分组间隔", "v1.4", "是", ""],
  ["roshan_attempt_damage_min", "1500", "伤害", "肉山尝试", "未击杀Roshan尝试最低伤害", "v1.4", "是", ""],
  ["roshan_attempt_kill_grace", "8", "秒", "肉山尝试", "伤害组结束后若8秒内击杀则不输出尝试", "v1.4", "是", ""],
  ["roshan_drop_merge_window", "20", "秒", "肉山击杀", "肉山击杀后掉落合并窗口", "v1.8", "是", ""],
  ["roshan_owner_drop_fallback", "5", "秒", "肉山击杀", "缺少roshan_death时用掉落阵营兜底归属", "v1.8", "是", ""],
  ["tormentor_alive_start", "1200", "秒", "魔晶团", "20分钟后魔晶/折磨者才可能存活", "v1.4", "是", ""],
  ["tormentor_respawn_suppression", "600", "秒", "魔晶团", "魔晶死亡后该时间内不视为存活", "v1.4", "是", ""],
  ["tormentor_near_radius", "1500", "码", "魔晶团", "独立魔晶击杀附近英雄判断", "v1.4", "是", ""],
  ["highground_raw_corner", "95/158", "raw坐标", "高地团", "高地团中心区域阈值", "v1.4", "是", ""],
  ["tower_context_radius", "1000", "码", "守塔团", "防御塔上下文范围", "v1.4", "是", ""],
  ["tower_context_time_pad", "5", "秒", "守塔团", "战斗窗口前后塔状态检测", "v1.4", "是", ""],
  ["aegis_valid_duration", "300", "秒", "带盾阵亡", "Aegis领取后可触发带盾阵亡的窗口", "v1.5", "是", ""],
  ["v1_7_subordinate_overlap_ratio", "0.60", "比例", "小规模冲突/团战/特殊战斗", "旧单点候选与父窗口重叠比例", "v1.7", "是", ""],
  ["v1_7_parent_start_grace", "2", "秒", "小规模冲突/团战/特殊战斗", "父窗口允许比候选开始最多晚2秒", "v1.7", "是", ""],
  ["v1_7_parent_end_grace", "5", "秒", "小规模冲突/团战/特殊战斗", "父窗口允许比候选结束最多早5秒", "v1.7", "是", ""],
  ["v1_8_duplicate_no_kill_gap", "3", "秒", "战斗类标签", "无独立击杀重复战斗候选合并最大间隔", "v1.8", "是", ""],
  ["v1_8_duplicate_distance", "1800", "码", "战斗类标签", "无独立击杀重复战斗候选中心最大距离", "v1.8", "是", ""],
  ["v1_8_duplicate_jaccard", "0.50", "比例", "战斗类标签", "无独立击杀重复战斗候选英雄Jaccard阈值", "v1.8", "是", ""],
  ["v1_8_duplicate_shared_heroes", "2", "人", "战斗类标签", "无独立击杀重复战斗候选共享英雄阈值", "v1.8", "是", ""],
  ["v1_9_kill_parent_gap", "5", "秒", "战斗类标签", "有击杀父事件吸收无独立击杀短窗口的最大相邻间隔", "v1.9", "是", ""],
  ["v1_9_kill_parent_distance", "2500", "码", "战斗类标签", "有击杀父事件吸收短窗口的最大距离；未知位置允许通过", "v1.9", "是", ""],
  ["v1_9_kill_parent_jaccard", "0.33", "比例", "战斗类标签", "有击杀父事件吸收短窗口的英雄Jaccard阈值", "v1.9", "是", ""],
  ["v1_9_kill_parent_shared_heroes", "2", "人", "战斗类标签", "有击杀父事件吸收短窗口的共享英雄阈值", "v1.9", "是", ""],
  ["adjacent_combat_qc_scan_window", "10", "秒", "质量控制", "生成后复查相邻战斗事件的建议扫描窗口", "v1.9", "是", ""],
];

const dataSourceRows = [
  ["match_info", "match_id, radiant_team_id, dire_team_id, duration", "比赛元数据", "全部", "定位比赛、阵营、时长", ""],
  ["match_picks_bans/players", "hero_id, localized_name, player_slot, team", "阵容与英雄映射", "全部", "建立英雄中文名、阵营、slot映射", ""],
  ["player_intervals2", "time, player_slot, x, y, kills, deaths, camps_stacked, creeps_stacked, rune_pickups", "英雄时序快照/计数", "对线分路; 控符; 囤野/堆野; 战斗参与; 资源附近英雄", "位置、堆野计数、附近英雄查找", ""],
  ["combat_logs", "time, type, attackername, targetname, inflictor, value, x, y", "伤害/死亡/技能/物品/控制/目标事件", "拉野; 勾兵; 断线; 对线区; 开雾; 战斗类; 肉山; 魔晶; 带盾阵亡", "核心事件流；需要英雄名解析、坐标转换、去重", ""],
  ["dota_model_neutral_siege_creep", "time, log_index, unit, team, ehandle, x, y, hp, max_hp, lifeState", "线上小兵状态/轨迹", "对线区; 勾兵; 断线", "按ehandle追踪小兵路线；判断死亡小兵500范围内是否有敌方线上小兵", "默认只需抽取-5到650秒"],
  ["match_chat_events", "time, type, player1, player2, value, log_index", "聊天/系统事件", "控符; 资源获得; 肉山击杀; 带盾阵亡; 英雄死亡兜底", "符、肉山、魔晶、盾、奶酪、买活、建筑等事件", ""],
  ["ward_placed_left_fact", "time, player_slot, x, y, ward_type", "眼位事件", "蹲人/视野复核", "当前v1.9不是主要自动输出依据，可供人工复核", ""],
  ["tower_status_update", "time, type, team_num, hp, max_hp, x, y", "防御塔状态", "守塔团", "判断战斗是否发生在存活防御塔附近", ""],
  ["derived: hero_pos", "hero, time, max_gap", "从player_intervals2派生", "全部空间规则", "给定英雄和时间返回位置；缺失时部分规则允许未知通过", ""],
  ["derived: objective_events", "roshan_death, miniboss_death, chat objective", "从combat/chat合并", "肉山团; 肉山击杀; 魔晶团; 资源获得", "资源上下文统一入口", ""],
  ["derived: lane_zone", "lane, source, x_min, x_max, y_min, y_max, sample_count", "从小兵正常交汇死亡点派生", "勾兵; 断线", "判断某路线小兵死亡是否发生在正常对线区外；本局样本不足时使用全局基准区域", ""],
  ["derived: fight_records", "start, end, center, heroes_by_team, damage_by_team, death_events", "战斗聚类结果", "GANK; 小规模冲突; 团战; 特殊战斗", "供后处理、标签判定、结果字段生成", ""],
  ["outputs/tactical_events_*_v1.5.json", "labels,status,time_range,heroes,region,evidence,notes", "原始事件候选", "v1.7-v1.9后处理", "保留位置/evidence，用于合并距离和父子窗口判断", ""],
  ["outputs/*_event_table_clean_v1.8.json", "精简事件表", "v1.9输入", "v1.9合并", "有击杀父事件吸收短窗口的输入源", ""],
];

const flowRows = [
  ["01", "读取比赛与阵容", "match_info; players; picks_bans", "建立match_id、阵营、英雄中文名、player_slot映射", "hero_text/slot_team/hero_team", "英雄名缺失会影响heroes和结果字段", "保留中文英雄名优先", ""],
  ["02", "读取时序与日志", "player_intervals2; combat_logs; match_chat_events; tower_status_update", "按时间排序，转换数值和坐标", "基础事件流", "坐标单位混用会造成距离误判", "使用raw/game转换并在证据中保留坐标", ""],
  ["02b", "生成对线区", "combat_logs; tower_status_update; 线上小兵位置快照", "用前10分钟双方一塔存活且附近存在敌方同路线小兵的死亡点，按路线生成lane_zone；样本不足则使用global_lane_zone", "derived: lane_zone", "小兵位置缺失会导致区域不可确认", "断线/勾兵必须引用同一lane_zone口径", ""],
  ["03", "生成事实类事件", "位置/计数/chat", "生成对线分路、囤野、控符、开雾、资源获得、带盾阵亡等", "初始候选事件", "控符和肉山最容易因重复日志产生重复行", "v1.5控符去重；v1.8肉山击杀合并", ""],
  ["04", "生成战斗检测项", "damage_events; control_events; death_events", "过滤伤害<45、无坐标、负时间；按10秒桶准备空间聚类", "detect_items", "远端消耗可能和边路冲突同桶", "v1.5通用空间/交互排除", ""],
  ["05", "空间聚类与细化", "detect_items", "同桶raw距离38聚类；跨桶12秒/48raw合并；再按前后6秒和62raw收集伤害/控制/死亡", "fight_records", "拆分过细或过粗都会影响后续标签", "后处理继续合并相邻重复", ""],
  ["06", "参与英雄过滤", "fight_records; hero_pos", "英雄需靠近战斗核心，或对核心参与者造成/承受伤害/控制；远处未命中核心目标的英雄排除", "heroes_by_team", "中路对线消耗误并入边路冲突", "规则不限10分钟前、不限中路，是通用排除规则", ""],
  ["07", "战斗标签判定", "fight_records", "先判断GANK：一方参与英雄>=2且另一方参与英雄<=1；否则按参与人数、直接参与人数、伤害、死亡、持续时间输出小规模冲突或团战", "战斗类事件", "边界事件仍需人工复核", "旧抓人候选不再作为最终标签，新增GANK承接N抓1语义", ""],
  ["08", "特殊战斗上下文", "fight_records; objective_events; tower_status_update", "给战斗追加肉山团、高地团、守塔团、魔晶团", "多标签战斗事件", "资源死亡后的追击误标资源团", "用资源存活约束和死亡后抑制窗口", ""],
  ["09", "历史单点候选重映射", "v1.5抓人候选; v1.5完整事件", "删除抓人标签；若有死亡补全结果；按重叠或人数归为团战/小规模冲突", "v1.7事件表", "删除标签后死亡结果丢失", "v1.6补全击杀结果；v1.7合并父子窗口", ""],
  ["10", "从属窗口合并", "v1.7战斗事件", "旧单点候选若与父战斗高重叠且无独立击杀或死亡已被父记录，则不单独输出", "减少重复战斗行", "短窗口边界比父事件略长导致漏合并", "父开始+2秒、父结束-5秒容差", ""],
  ["11", "肉山击杀合并", "v1.7资源获得", "把击杀肉山和20秒内盾/奶酪/刷新碎片/战旗合并为肉山击杀", "v1.8事件表", "CHAT_MESSAGE_ROSHAN_KILL误当某英雄击杀", "归属优先combat_logs roshan_death", ""],
  ["12", "无击杀重复战斗合并", "v1.8前战斗事件", "时间重叠/间隔<=3秒、距离<=1800、英雄重合达标且均无独立击杀则合并", "v1.8事件表", "重复小窗口仍可能保留", "输出合并mapping供审计", ""],
  ["13", "有击杀父事件吸收短窗口", "v1.8事件表", "有死亡/击杀结果的父事件吸收相邻或重叠的无独立击杀短窗口", "v1.9事件表", "v1.8漏掉父事件有击杀、子窗口只是BKB/TP/短控制的重复", "v1.9新增5秒/2500码/33%重合规则", ""],
  ["14", "导出Excel", "v1.9 JSON", "按定义列输出战术事件表，并生成标签计数和定义版本页", "战术事件_matchid.xlsx", "批注列缺失或边框不一致", "固定8列，所有单元格框线", ""],
  ["15", "质量复查", "最终Excel/JSON", "扫描相邻10秒内战斗事件；检查共享英雄、同区域、同击杀链和资源事件归属", "复查结论", "同类重复可能随新比赛出现", "v1.9把原因写入规则，后续可继续收敛", ""],
];

const mergeRows = [
  ["M01", "控符单神符去重", "控符", "同英雄同神符先RUNE_BOTTLE后RUNE_PICKUP，且拾取在罐装后0-90秒", "保留罐装事件，删除后续拾取事件", "避免8分钟双倍、10分钟隐身等一颗符两行", "v1.5", "是", ""],
  ["M02", "能量符未刷新侧补记", "控符", "能量符实际刷新在一侧，另一侧同刷新点1000码内有英雄", "另一侧输出控符事件，结果=神符刷新在另一侧", "记录控符但未控到符的站位行为", "v1.5", "是", ""],
  ["M03", "远端未交互英雄排除", "战斗类标签", "英雄距离战斗核心过远，且未对核心参与英雄造成伤害/控制，也未被核心参与英雄击杀/控制", "不列入heroes，不作为先手/伤害证据", "修正中路对线消耗误识别参与边路冲突，本质不限时间和路线", "v1.5", "是", ""],
  ["M04", "旧抓人标签删除", "历史抓人候选", "labels含抓人", "删除抓人标签；按规则改为GANK/小规模冲突/团战或合并入父事件", "v1.5后最终定义不再有抓人字段，v1.10新增GANK", "v1.6-v1.10", "是", ""],
  ["M05", "旧单点死亡结果补全", "历史抓人候选", "旧单点候选记录了被抓英雄且造成死亡", "结果写击杀比分和死亡英雄，如击杀结果：天辉1-0夜魇；死亡：05:34祈求者", "修复死亡未记录结果", "v1.6", "是", ""],
  ["M06", "旧单点候选归类", "历史抓人候选", "符合N抓1则GANK；与已有团战窗口重叠则团战；与已有小规模冲突重叠则小规模冲突；否则总人数>=8且双方>=3为团战，其余为小规模冲突", "输出新标签，不输出抓人", "删除抓人后仍保留战斗语义", "v1.6/v1.7/v1.10", "是", ""],
  ["M07", "从属候选并入父窗口", "战斗类标签", "候选与父战斗重叠>=60%；父开始<=候选开始+2秒；父结束>=候选结束-5秒；且无独立击杀或死亡已由父事件记录", "删除子候选独立行，保留父事件", "修复205包含206-210、215包含216-217等父子重复", "v1.7", "是", ""],
  ["M08", "父窗口来源扩展", "战斗类标签", "父窗口既可来自原生战斗事件，也可来自旧单点候选中已造成死亡并被重映射的事件", "扩大可吸收范围", "214应合并到215一类问题", "v1.7", "是", ""],
  ["M09", "肉山击杀与掉落合并", "肉山击杀", "资源获得中结果含击杀肉山，且后续0-20秒内有盾/奶酪/刷新碎片/战旗掉落", "输出一行肉山击杀；结果串联合并且去重；删除掉落单独资源行", "修复23:37拉比克误被当资源获得，实际为夜魇击杀肉山", "v1.8", "是", ""],
  ["M10", "Roshan归属来源优先级", "肉山击杀", "CHAT_MESSAGE_ROSHAN_KILL存在player/value但combat_logs roshan_death也存在", "使用combat_logs roshan_death阵营作为击杀方；chat字段只作事件事实", "避免把系统字段误解释为击杀英雄", "v1.8", "是", ""],
  ["M11", "无击杀相邻战斗重复合并", "战斗类标签", "两个战斗均无独立击杀；时间重叠或间隔<=3秒；距离<=1800；英雄Jaccard>=50%或共享英雄>=2；且有战斗标签重叠", "合并time_range、labels、heroes；结果=未记录英雄击杀/死亡无", "修复110-111、144-145等无击杀重复", "v1.8", "是", ""],
  ["M12", "有击杀父事件吸收无击杀短窗口", "战斗类标签", "一行有死亡/击杀结果，另一行无独立击杀；时间重叠或间隔<=5秒；距离<=2500或未知；共享英雄>=2或Jaccard>=33%", "保留有击杀父事件结果；time_range覆盖父子；heroes并集；删除短窗口", "v1.9核心修复：父事件完整、子事件只是逃脱/短控制/短伤害", "v1.9", "是", ""],
  ["M13", "相邻战斗人工复查", "质量控制", "最终表中相邻10秒内战斗事件共享英雄、同区域、同击杀链或一方仅微动作", "优先判定是否合并；若确为不同战斗再保留", "持续减少同类重复识别", "v1.9", "是", ""],
];

const outputRows = [
  ["id", "事件序号", "是", "按time_range排序后从1开始", "数值", "由导出器重排", ""],
  ["match_id", "比赛ID", "是", "字符串或数字均可，同一表内保持一致", "文本/数字", "来源比赛列表", ""],
  ["labels", "事件标签", "是", "多个标签用 / 分隔；不得包含抓人", "文本", "来自定义清单19标签", ""],
  ["confidence", "置信度", "是", "0-1；事实类较高，候选类较低", "数值", "识别器赋值", ""],
  ["time_range", "事件时间段", "是", "格式mm:ss-mm:ss；瞬时事件起止可相同", "文本", "由事件start/end格式化", ""],
  ["heroes", "参与/附近英雄", "是", "战斗类为真实参与英雄；控符为符点附近双方英雄；资源类为获得方/英雄", "文本", "按标签语义生成", ""],
  ["结果", "事件结果", "否", "击杀/死亡、资源、控符结果等；无结果可留空", "文本", "后处理可补全或合并", ""],
  ["批注", "人工复核备注", "否", "最后一列，默认空白", "文本", "供人工修改", ""],
];

const editRows = [
  ["E01", "开启/关闭标签", "标签算法", "自动状态", "把auto/candidate_review改为禁用或人工", "影响该标签是否自动输出", "需要同步脚本", ""],
  ["E02", "调整阈值", "参数阈值", "当前值", "例如把v1_9_kill_parent_gap从5改为8", "影响召回率/误合并率", "最建议从这里改", ""],
  ["E03", "改数据源", "数据源字段", "使用标签/关键解释", "增加新表或字段", "影响算法可实现性", "需要确认数据库字段存在", ""],
  ["E04", "改处理顺序", "处理流程", "step_id/处理", "例如先合并肉山再合并战斗", "可能影响结果优先级", "改动需回归五局", ""],
  ["E05", "改合并规则", "合并去重", "触发条件/动作", "例如要求距离未知时不允许合并", "影响重复事件数量", "建议保留版本来源", ""],
  ["E06", "改输出字段", "输出格式", "列名/备注", "例如新增复核状态列", "影响所有最终Excel格式", "后续表格生成都应同步", ""],
  ["E07", "人工复核重点", "标签算法/合并去重", "人工复核点/要解决的问题", "记录当前误识别样例和处理意见", "用于下一版规则沉淀", "可直接填写批注", ""],
];

const workbook = Workbook.create();

writeSheet(workbook, "版本说明", ["项目", "内容", "说明", "批注"], versionRows, [160, 280, 620, 180], 46);
writeSheet(
  workbook,
  "标签算法",
  ["label", "自动状态", "分类", "核心数据源", "触发条件", "时间窗口", "空间/阈值", "参与英雄口径", "结果字段算法", "合并/去重规则", "优先级/互斥", "人工复核点", "版本来源", "批注"],
  labelRows,
  [110, 110, 65, 220, 420, 260, 300, 360, 360, 360, 280, 280, 110, 180],
  78,
);
writeSheet(
  workbook,
  "参数阈值",
  ["参数名", "当前值", "单位", "适用标签", "作用", "来源版本", "可人工修改", "批注"],
  parameterRows,
  [230, 100, 90, 220, 480, 90, 100, 240],
  44,
);
writeSheet(
  workbook,
  "数据源字段",
  ["数据源", "字段", "类型/含义", "使用标签", "关键解释", "批注"],
  dataSourceRows,
  [230, 360, 220, 330, 440, 180],
  60,
);
writeSheet(
  workbook,
  "处理流程",
  ["step_id", "阶段", "输入", "处理", "输出", "失败/误识别风险", "v1.9修正", "批注"],
  flowRows,
  [70, 180, 260, 440, 220, 300, 360, 180],
  66,
);
writeSheet(
  workbook,
  "合并去重",
  ["rule_id", "规则名称", "适用标签", "触发条件", "动作", "要解决的问题", "来源版本", "可人工修改", "批注"],
  mergeRows,
  [80, 220, 180, 500, 360, 360, 90, 100, 180],
  68,
);
writeSheet(
  workbook,
  "输出格式",
  ["列名", "含义", "是否必填", "备注", "数据类型", "生成方式", "批注"],
  outputRows,
  [120, 180, 90, 500, 100, 220, 180],
  52,
);
writeSheet(
  workbook,
  "人工修改入口",
  ["edit_id", "修改对象", "所在sheet", "建议修改列", "修改示例", "影响", "落地建议", "批注"],
  editRows,
  [80, 160, 160, 160, 380, 260, 260, 180],
  58,
);

await fs.mkdir(outputDir, { recursive: true });
await fs.mkdir(previewDir, { recursive: true });

const previewRanges = [
  ["版本说明", "A1:D9"],
  ["标签算法", "A1:N12"],
  ["参数阈值", "A1:H28"],
  ["数据源字段", "A1:F13"],
  ["处理流程", "A1:H16"],
  ["合并去重", "A1:I14"],
  ["输出格式", "A1:G9"],
  ["人工修改入口", "A1:H8"],
];

for (const [sheetName, range] of previewRanges) {
  const preview = await workbook.render({ sheetName, range, scale: 1, format: "png" });
  const safe = sheetName.replace(/[\\/:*?"<>|]/g, "_");
  await fs.writeFile(path.join(previewDir, `${safe}.png`), new Uint8Array(await preview.arrayBuffer()));
}

const formulaErrors = await workbook.inspect({
  kind: "match",
  searchTerm: "#REF!|#DIV/0!|#VALUE!|#NAME\\?|#N/A",
  options: { useRegex: true, maxResults: 100 },
  summary: "formula error scan",
});

const sample = await workbook.inspect({
  kind: "table",
  range: "标签算法!A1:N20",
  include: "values",
  tableMaxRows: 20,
  tableMaxCols: 14,
});

const exported = await SpreadsheetFile.exportXlsx(workbook);
await exported.save(outputPath);

console.log(JSON.stringify({
  outputPath,
  previewDir,
  sheets: 8,
  rows: {
    versionRows: versionRows.length,
    labelRows: labelRows.length,
    parameterRows: parameterRows.length,
    dataSourceRows: dataSourceRows.length,
    flowRows: flowRows.length,
    mergeRows: mergeRows.length,
    outputRows: outputRows.length,
    editRows: editRows.length,
  },
  formulaErrorScan: formulaErrors.ndjson,
  sample: sample.ndjson.split("\n").slice(0, 2).join("\n"),
}, null, 2));
