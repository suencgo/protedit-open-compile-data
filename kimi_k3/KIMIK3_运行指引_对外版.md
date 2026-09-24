# ProtEdit Compile 任务交接 —— Kimi-K3 操作指引（2026-09-24）

> 一句话背景：ProtEdit 蛋白质编辑 benchmark 需要 6 个 LLM 把 350 个任务卡编译成
> 三个蛋白质生成后端（boltzgen / odesign / rfd3）的输入 spec（JSON）。这一批由
> 你用 Kimi-K3 完成，共 **850 条**（350 任务 × 3 后端，已剔除平台不支持的组合）。

---

## 第一步：拿代码（公开仓库，无需权限）

```bash
git clone https://github.com/suencgo/protedit-open-compile-data.git
cd protedit-open-compile-data/kimi_k3
```

网络受限拿不了全仓库的话，最少只需要三个目录：
`kimi_k3/`（输入+说明）、`validation/`（自检器）、`tasks/`（任务卡，自检时用）。

## 第二步：理解输入

`kimi_k3_prompts.jsonl` 共 850 行，每行一个编译单元：

```json
{"task_id": "ptask-xxxx", "backend": "odesign", "action": "move_away",
 "system": "You are a protein design engineer...",
 "user": "# Backend Capability Card\n...\n# Task Card\n{...}\nOutput the JSON input specification now."}
```

**把 system + user 原样作为两条消息发给 Kimi-K3，不要改写、不要拼接、不要加料。**

## 第三步：调用 Kimi-K3（参数要点）

- **不要传 temperature**（Kimi-K3 网关拒绝该参数）
- max_tokens **8192**；不传 reasoning effort（用默认档）
- 响应若含 `reasoning_content` 字段，**只取 `choices[0].message.content`**
- **两轮修正协议**：若输出不是合法 spec（见第四步格式），追加两条消息再问一次：
  `{"role":"assistant","content":"<上一轮原文>"}` +
  `{"role":"user","content":"Your output was invalid:\n{错误列表}\nReturn a corrected JSON object only."}`
  最多 2 轮，仍失败记 invalid（保留原文和错误）
- JSON 提取：容忍 markdown 代码围栏（```json ... ```），取文本中的单个 JSON 对象

**一个完整往返的例子**（rfd3 后端，alpha_rich 任务，源链 A 长 490、edit_core 267-314）：
发 system+user → Kimi 回（可以带围栏）：
```json
{"contig": "A1-266,48-48,A315-490", "length": "490"}
```
这就是一份合法 spec，存盘即可。

## 第四步：输出格式（目录约定，务必遵守）

每条产出写三个文件：

```
kimi-out/compile/kimi-k3/<backend>/<task_id>/spec.json          # 最终 spec（单 JSON 对象）
                                    /attempt1.response.txt       # Kimi 原文（每轮都留）
                                    /compile_record.json         # 见下
```

`compile_record.json`：
```json
{"task_id":"ptask-xxxx","backend":"odesign","status":"ok",
 "usage":{"prompt_tokens":1234,"completion_tokens":567},
 "attempts_used":1,"errors":[]}
```
（失败时 status=invalid，errors 填验证错误前几条；网络失败 status=llm_error。）

**spec 顶层字段（按后端）**：
- odesign：`{"chains":[...], "hotspot":"...", "partial_diff":"..."}`
- boltzgen：`{"entities":[{"file":{"include":[...],"design":[...]}}]}`
- rfd3：`{"contig":"A1-266,48-48,A315-490", "length":"490"}`

不要输出 ref_file、采样参数或解释性字段；格式细节以 prompt 里的 Capability Card 为准。

## 第五步：自检（强烈建议，出问题当场发现）

```bash
cd protedit-open-compile-data
python3 validation/run_validation.py --bundle . --tree kimi-out/compile
```
期望输出 `failures: 0`。纯标准库，无需安装。（注意：机器若有公司代理，先
`export no_proxy=...` 或 unset 代理变量，否则可能连不上你的 API 端点。）

## 第六步：交回什么

整个 `kimi-out/compile/` 目录打包（tar.gz）发回。后续 GPU 生成和评分不归你管。

## 常见坑

1. **别改 prompt 内容**——它是实验变量，改了数据就作废
2. 不支持的组合已从 jsonl 剔除（850 条全是需要作答的），不要自己补任务
3. 断点续跑：按行处理，已存在合法 spec.json 的 task 直接跳过
4. 规模参考：850 次调用（多数一轮成功，少量两轮），按需分批（比如按 backend 分三批）
5. 唯一权威文档在仓库里：`kimi_k3/KIMI_K3_使用说明.md`、`README_迁移说明.md`（有疑问先查）
