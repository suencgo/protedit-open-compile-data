# ProtEdit Open-Compile 交接说明（2026-09-24）

## 1. 交接物

| 仓库（均私有，账号 suencgo） | 内容 | 用途 |
|---|---|---|
| `protedit-open-compile-data` ★ | 代码 + 全量数据（含 186 个源结构 cif） | **交接主仓库**，克隆即用 |
| `protedit-open-compile` | 同套代码，不含结构二进制 | 轻量镜像 |

本地工作区（Mac）：`~/Documents/kimi/tasks/2026-09-22/12-37-52-85d726cf/`
（分析底账 analysis/、实验计划与交接文档、迁移包 tar）。
远端（Muxi，端口 20268，见项目交接文档 §4）：实验根
`/datashare/suencheng/protedit_v2/open_compile_2x2_20260924/`。

## 2. 五分钟上手（在任意有 LLM 额度的机器）

```bash
git clone https://github.com/suencgo/protedit-open-compile-data.git && cd protedit-open-compile-data
export OPENAI_BASE_URL=<OpenAI兼容端点> OPENAI_API_KEY=<key>   # 有公司代理时设 no_proxy！
python3 runner/compile_remote_reference.py --bundle . --tier t1 --out ./out   # 稍高自由度 0.2℃
python3 runner/compile_remote_reference.py --bundle . --tier t2 --out ./out   # 高自由度 0.7℃
python3 validation/run_validation.py --bundle . --tree ./out/t1/compile       # 自检（可对 t2 再跑一次）
```

纯标准库、零安装。参数、协议、输出格式细节见 `README_迁移说明.md`（必读）。
产出 `out/t1|t2/compile/<model>/<backend>/<task>/{spec.json,compile_record.json,...}`
树发回 Muxi 对应目录后接 GPU 生成与评分。

## 3. 实验状态快照（2026-09-24 UTC）

- **t0 基线**：已有（matrix 冻结树），坍缩率 boltzgen 23% 单一 / odesign 42% / rfd3 98%
- **tc 臂（原卡 0.7℃）**：✅ 完成（ok 5066，invalid 33）。**结论：温度不打破坍缩**
  （三后端唯一 spec 分布与 t0 几乎逐格相同；rfd3 200/200 仍单一）
- **t1 臂（开放卡 0.2℃）**：⛔ 446/5098 单元后被 Boyue 网关额度耗尽卡住
  （余额 $-0.57）。launcher 在 Muxi 自动重试至 2026-09-26 03:58Z 截止；
  **充值即自愈，无需人工干预**
- **t2 臂**：未开始（排队于 t1 后）
- **GPU 试点（10 任务×16 rollout，boltzgen）**：✅ 两臂生成完成
  （t1-GPT spec 182 候选 / ZCode 手写 spec 181 候选，各 10/10 任务 ok），
  **评分待跑**——在 Muxi 执行：
  `bash /datashare/suencheng/protedit_v2/open_compile_2x2_20260924/pilot10/bin/score_pilot.sh`
  （gpt 树）；zcode-self 树把脚本里 MODEL 改为 zcode-self 再跑一次。
  编译层已知差异：ZCode 手写 spec 7/10 不同于 t0，t1-GPT 仅 2/10
  （详见 `experiment/my_specs_zcode.json` 注释与本地差异表）
- **burial_control 替换 shape_control**：已评估并**放弃**——50 任务源核心
  median RSA 抽样 15/50 全部 ≤0.26（核心本已埋藏，delta≤-0.10 不可达）。
  取证脚本：Muxi `bin/burial_precondition_check.py`

## 4. 未决事项（按优先级）

1. Boyue 充值 → 等 t1/t2 自动跑完 → `bin/collapse_report.py` 出四臂坍缩终表
2. GPU 试点评分 → t0 vs GPT-开放 vs ZCode 三方 16-rollout 对照表
3. 按坍缩率选臂 → Phase 1 GPU 波（≈4.27 万候选，预算先报再跑）
4. （背景）test split 308 终评协议等，见项目交接文档 §9

## 5. 已踩坑清单（新人必读）

- 公司代理拦 github.com 与 localhost：git/curl 加 `--noproxy '*'` /
  `no_proxy` 环境变量；llm runner 同理（README §7）
- ClusterX job 名必须**小写+连字符**（`pocA0` 报 400；`poc10a-s0-t1gpt` 正常）；
  提交间隔 ≥60s；对账只信 shard_summary.txt/worker.log marker，不信 clusterx list
- Muxi sshd 限流：合并单条 SSH，reset 后退避 ≥5 分钟
- BCC 卡内容是实验变量，**一个字都不能改**；评分器/任务卡/worker 契约全程冻结
- 评分 manifest 时间必须晚于最后分片完成（项目交接文档陷阱 4）
- 大 git 推送（77MB+）直连可能被断：`http.postBuffer=524288000` 重试即可

## 6. 联系与上游文档

- 项目全量交接：`PROTEDIT_交接文档_20260924.md`（Mac 工作区）
- 实验设计与计划：`OPEN_COMPILE_2x2_实验计划_20260924.md`（同上）
- Gate 判定逻辑：`agent/docs/GATE_LOGIC.md`（评分器源码级）
- 判别性统计方法：`analysis/COVERAGE_SIGNIFICANCE_20260924.md`、
  `analysis/FORENSICS_跨模型一致性_20260924.md`
