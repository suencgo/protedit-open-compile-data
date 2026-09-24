# ProtEdit Open-Compile 迁移包（2026-09-24）

> 目的：在另一台机器上运行 6 个 LLM × 3 个生成后端 × 350 任务 × 2 档自由度的
> compile（spec 生成），产物拿回 Muxi 后由本地方接手 GPU 生成与评分。
> Boyue 网关额度耗尽是迁移原因；GPU/评分不受影响。

## 1. 两档 prompt（自由度版本）

| 档 | 名称 | system prompt | BCC 卡 | temperature | 说明 |
|---|---|---|---|---|---|
| **t1（稍高自由度）** | 开放决策·低温 | `prompts/system_t1_t2_open.txt` | `prompts/bcc_open/bcc_{backend}.md` | **0.2** | 纯模板效应臂 |
| **t2（高自由度）** | 开放决策·高温 | 同上（与 t1 完全相同） | 同上（与 t1 完全相同） | **0.7** | 模板+采样双因素臂 |

- t1 与 t2 的 system + user 消息**逐字节相同**，唯一差异是 temperature 采样参数
  → `prompts/user_messages/{backend}/{task_id}.txt` 一套通用。
- 附带 `prompts/bcc_orig/`（原版强模板卡）与 `prompts/system_tc_orig.txt`，
  如需补跑 tc 对照臂（原卡 0.7℃）可直接使用，非必须。
- user 消息拼装顺序（与 matrix compile_driver 逐字一致）：
  `# Backend Capability Card\n{卡}\n\n# Scoring Criteria\n{评分卡}\n\n# Task Card\n{任务卡JSON}\n\nOutput the JSON input specification now.`
- 任务卡已剥离 oracle 键（routing / backend_hint / required_primitives /
  backend_support / allowed_backends / preferred_backend / support_reason）。
- rfd3 开放变体配套**放宽验证器**（生成段允许 edit_core 子集覆盖），
  卡内硬合同第 1 条已改写；od/bg 的 schema 与原卡逐字相同，只是决策放开。

## 2. 模型参数（6 模型，全部经同一 Boyue OpenAI 兼容网关）

| 模型 | model_id | 推理 | supports_temperature | max_tokens | timeout | max_attempts |
|---|---|---|---|---|---|---|
| gpt-5.6-terra | `gpt-5.6-terra` | 否 | 是 | 8192 | 300s | 5 |
| claude-opus-5 | `claude-opus-5` | 否 | **否（勿传 temperature）** | 8192 | 300s | 5 |
| kimi-k3 | `kimi-k3` | **是** | **否（勿传 temperature）** | 8192 | 300s | 5 |
| glm-5.3-flash | `glm-5.3-flash` | **是** | 是 | 8192 | 300s | 5 |
| deepseek-v4-flash | `bailian/deepseek-v4-flash` | 否 | 是 | 8192 | 300s | 5 |
| gemini-3.5-flash | `gemini-3.5-flash` | 否 | 是 | 8192 | 300s | 5 |

**推理档位说明（重要）**：调用 payload **不携带任何 reasoning effort / thinking 参数**
（无 medium/high 之类字段）。kimi-k3 与 glm-5.3-flash 是推理模型，使用网关默认档位；
响应中若出现 `reasoning_content` 字段，**必须剥离，只取 `choices[0].message.content`**。
若你的新网关支持并要求显式 reasoning 档位，请记录所用档位于 compile_record.meta，
并在回迁时告知（这本身是协议偏差，需要标注）。

**payload 精确形态**（POST `{api_url}`，`Authorization: Bearer {key}`）：
```json
{"model": "<model_id>", "messages": [...], "max_tokens": 8192,
 "temperature": 0.2}   // 仅 supports_temperature=true 的模型携带；t2 传 0.7
```
- 无 stream、无 top_p、无 stop、无其他采样参数。
- 重试：429/5xx/传输错误指数退避 `min(120, 5*2^attempt)`，最多 5 次；
  401/403 换 key 立即重试（无 key 池则直接失败）。

**两轮修正协议**：attempt 1 发 system+user；若输出非法（非单 JSON 或验证不过），
attempt 2 追加 `assistant: <上次原文>` + `user: "Your output was invalid:\n{errors}\nReturn a corrected JSON object only."`。
两轮都失败记 invalid（errors 保留前 12 条）。

**JSON 提取**：容忍代码围栏与前后空白，取文本中的单个 JSON 对象
（参考实现见 `runner/compile_remote_reference.py` 的 extract_json）。

## 3. 调用范围与跳过规则

- 全量：6 模型 × 3 后端 × 350 任务 × 2 档 = 12,600 单元，其中按能力矩阵
  **跳过 unsupported 组合**（不调 LLM，直接记 `backend_unsupported`）：
  - odesign × head_to_tail_cyclization（50 任务）
  - rfd3 × head_to_tail_cyclization / move_away / move_closer（150 任务）
  - 实际 LLM 调用 ≈ **5,098 × 2 档 ≈ 10,196 次**
- 能力矩阵全文见 `params/capability_matrix.json`（boltzgen 全支持；od 的
  move_* 为 native；其余多为 partial——partial 也要正常编译）。
- 任务清单：`tasks/subset_task_list.json`（350 个 ptask id，顺序无关）。

## 4. 输出约定（回迁对接格式，务必遵守）

每单元一个目录，落在所选档名下（t1 或 t2）：

```
<t1|t2>/compile/<model>/<backend>/<task_id>/
    spec.json              # 成功：单一 JSON 对象（schema 见对应 BCC）
    compile_record.json    # {"task_id","model","backend","tier","action",
                           #  "status": ok|invalid|backend_unsupported|llm_error,
                           #  "usage":{"prompt_tokens","completion_tokens"},
                           #  "latency_seconds","attempts_used","errors":[]}
    attempt1.response.txt  # 原始回复全文（两个 attempt 都留）
    attempt2.response.txt  # 仅当用了第二轮
    invalid.json           # 仅失败时：{"errors":[...]}
```

- 不支持的组合也要写 compile_record.json（status=backend_unsupported，usage 全 0）。
- `spec.json` 顶层键：odesign=`{chains,hotspot,partial_diff}`；boltzgen=
  `{entities[,constraints]}`；rfd3=`{contig,length}`。不要输出 ref_file、采样参数、
  额外解释字段——验证器会拒。
- 建议在远端先跑 `validation/run_validation.py` 自检（不依赖 Muxi）：
  od/bg 用 `validate_llm_spec.py`；rfd3 用 `validate_rfd3_open.py`（开放子集规则）。

## 5. 回迁步骤（拿到输出后）

1. 把 `t1/compile/` 与 `t2/compile/` 两棵树打包发回，落到
   `/datashare/suencheng/protedit_v2/open_compile_2x2_20260924/<tier>/compile/`。
2. 通知本地方；后续自动流程：spec md5 坍缩报告 → 选臂 → 按后端 worker 契约
   分片提交 ClusterX 生成（boltzgen YAML / odesign INPUT_JSON / rfd3 contig）→
   冻结评分器（core_redesign_v2 profile）评分 → 三口径对照表。

## 6. 目录清单

```
prompts/  system_t1_t2_open.txt, system_tc_orig.txt, bcc_open/×3, bcc_orig/×3,
          criteria_card_core_redesign_v1.md, user_messages/{backend}/<task>.txt ×1050
tasks/    subset_task_list.json, cards/<task_id>.json ×350（oracle 剥离版）
params/   model_params.json, capability_matrix.json
runner/   compile_remote_reference.py（参考实现：无 key，环境变量注入）
validation/ validate_llm_spec.py, rfd3_lane.py, validate_rfd3_open.py, run_validation.py
README_迁移说明.md  ← 本文件
```

## 7. 注意事项

- **代理陷阱（实测踩过）**：runner 用 urllib，遵循 `http_proxy/https_proxy/no_proxy`
  环境变量。若目标端点是内网/直连地址而机器设了公司代理，必须
  `export no_proxy=<端点host>` 或 unset 代理变量，否则请求会被发去代理并超时。
- key 不随包迁移（原 key 在 Muxi /root/ 下，600 权限）。新机器用你自己的
  OpenAI 兼容端点与 key；若模型名不同，在 runner 的 MODEL_<NAME>_ID 等环境变量里
  映射（名字清洗规则：大写、`-`/`.` 换 `_`，如 `MODEL_GLM_5_3_FLASH_ID`），
  并保留 registry 原 model_id 记录在 compile_record.meta。
- 温度语义差异是已知风险：不同供应商的 0.7 分布不等价，回迁时注明端点。
- 千万不要改 BCC 卡内容再跑——模板是实验变量，改了就不是 t1/t2 了。

## 8. 生成阶段（※确认留在 Muxi 执行，本节仅为参考材料）

> 工作流已确认：**目标机器只跑 §1–§4 的 LLM compile**（那边有 API 额度）；
> 三个生成后端与评分仍在 Muxi 的 ClusterX 上跑，后端环境/权重（~87GB）不迁移。
> 本节内容（源结构、worker 脚本、prep 工具）体积小，随包附带仅作备份与参考，
> Muxi 上原本就有全套。

**随包附带**（`structures/` + `workers/` + `runner/generation_prep.py`）：

- `structures/<psource-id>/structure.cif` + `canonicalization.json`：
  350 任务全部源结构（136 个唯一源，59.9MB，零缺失）
- `structures/manifest.json`：task_id → psource 映射
- `workers/`：三个后端的 worker 脚本原样拷贝
  （boltzgen markers worker、odesign seed43 worker、rfd3 shard worker，
  另附 pilot_shard_worker.sh = 每任务 16 设计的分片驱动变体）
- 一键转换：`python runner/generation_prep.py --bundle . --specs <tier>/compile/<model>
  --out genprep/` → 产出三个后端的完整输入（boltzgen per-task YAML 已注入
  结构路径；odesign input.json；rfd3 inputs.json），结构路径按 prep 运行时所在机器解析为绝对路径（worker 直接可用）。

**未随包携带（太大，~87GB，需单独搬运）**——目标机器"什么都没有"的话必须补齐：

| 内容 | Muxi 路径 | 大小 |
|---|---|---|
| 后端运行环境 | `handoff-clusterx-20260812/protein_edit/envs/`（boltzgen、boltzgen-maca、odesign、odesign-maca、maca-pytorch-base） | 20G |
| 模型权重 | `protein_edit/checkpoints/`（boltzgen、odesign） | 25G |
| 模型源码/第三方 | `protein_edit/sources/`（ODesign、ProteinMPNN、boltzgen） | 38G |
| rfd3 bundle | `protedit_v2/envs/rfd3-v1-0917/` | 4.1G |

建议用移动硬盘/rsync 整目录拷贝（保持目录结构不变，worker 脚本内的路径
假设 `PE_ROOT=.../protein_edit` 与 `RFD3_BUNDLE=.../rfd3-v1-0917`）。
GPU 需求：boltzgen/odesign 单卡可跑；rfd3 建议 28 CPU/64G 资源档。

生成产物请按 `runs/<model>/<backend>/r2/shard-*/tasks/<task_id>/*.cif +
worker.log(status=ok)` 的目录约定摆放（含 status=ok 的 worker.log 是评分
器发现候选的必要 marker），打包发回后本地方接冻结评分器评分。
