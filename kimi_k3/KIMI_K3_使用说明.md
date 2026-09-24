# Kimi-K3 独立作答包

给一个 Kimi-K3 实例（coding plan）独立完成 ProtEdit compile 用。

## 输入
`kimi_k3_prompts.jsonl`：850 条（350 任务 × 3 后端，已剔除平台不支持的组合）。
每行 = {task_id, backend, action, system, user}——**直接把 system + user 作为
两条消息发给 Kimi，不要改写**。

## 调用要点（Kimi-K3 专属）
- **不要传 temperature**（Kimi-K3 网关拒绝该参数）
- max_tokens 8192；不传 reasoning effort（用默认档）
- 响应若含 reasoning_content 只取 content 字段
- 两轮修正协议：输出非法时把上一轮原文 + "Your output was invalid: {errors}
  Return a corrected JSON object only." 作为追加消息再问一次（最多 2 轮）
- JSON 提取：容忍 markdown 代码围栏，取单个 JSON 对象

## 输出约定
每条产出写一个文件：
```
kimi-out/compile/kimi-k3/<backend>/<task_id>/spec.json      # 单 JSON 对象
                               /attempt1.response.txt        # 原文留档
                               /compile_record.json          # {"status":"ok","usage":{...},...}
```
spec 顶层字段：odesign={chains,hotspot,partial_diff}；
boltzgen={entities[,constraints]}；rfd3={contig,length}。

## 自检（可选但强烈建议）
把 kimi-out/compile 拷回本仓库目录后：
```bash
python3 validation/run_validation.py --bundle . --tree kimi-out/compile
```
0 失败即合格。合格后整棵树发回 Muxi 接 GPU 生成。

## 说明
- Kimi 无法分 t1/t2 两档（不接受温度参数）→ 本包产出视为单一档
  "kimi-default"（开放模板、默认采样），回迁时在 compile_record.meta 注明。
- prompt 内容与主实验开放模板逐字一致，**不要修改**。
