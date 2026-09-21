# 桌面论文的本地试点

这是实际运行记录，不是公开排行榜复述。原始 PDF、抽取全文、向量实验结果和模型缓存都留在忽略的 `state/paper-pilot/`，没有上传论文；未修改 Desktop 原文件。

## 语料与解析

- 31 个 PDF，SHA-256 精确去重后 30 篇，共 795 个物理页面。文件名相似并不代表同一版本，不做模糊去重。
- pypdf 全页抽取与 SQLite trigram FTS 建库约 8.92 秒，最终 0 个文件失败。两份 PDF 的字体映射产生孤立 Unicode surrogate，已用替代字符处理并在 manifest 标记，不能认为公式提取无损。
- 70 页文字少于 100 字，包含空白页、图像页等；这只是 OCR/人工复核候选数，不是已确认失败率。
- 检索 `two-step prefetching` 实际返回 FlashGS 物理第 2 页，已渲染该页检查双栏原文。返回路径、SHA-256 与页序号。
- Docling 2.129.0 解析 FlashGS 第 1–3 页成功，43 个文本块均带 provenance，保留页码与 bounding box。首次约 60.51 秒，包含模型初始化/下载，不能直接与全库 pypdf 时间比较。
- 中文学位论文的第 7 页摘要成功解析，11 个文本块，已渲染原页核对段落顺序；缓存模型后约 3.85 秒。另取第 10–12 页（英文摘要尾页和目录）成功解析，约 9.54 秒。未验证扫描 OCR、复杂公式、跨页表格。

## 检索试验方法

从每篇前三页构造 206 个文本片段，长度 2,000 字符、重叠 200 字符，模型最大输入 1,024 tokens。共 4 个手工构造的已知来源问题，包含中英跨语言检索。使用本机 MPS、float32、batch=4；Qwen 使用官方建议的 query instruction，BGE 使用默认 dense embedding。

这不是完整795页的语义索引，也不是正式盲测。只标注一个目标证据页，其他页面可能同样相关；不能把目标页排名等同于完整 relevance judgment。BGE 的 sparse/multi-vector 能力和 reranker 未参测。长片段还存在 token 截断风险，正式导入应按 token/段落切分。

| 指标 | Qwen3-Embedding-0.6B | BGE-M3 dense |
|---|---:|---:|
| 中文：两阶段预取加速渲染，目标 FlashGS p2 | 1 | 3 |
| 英文：4 倍加速与 49% 内存节省，目标 FlashGS p2 | 1 | 3 |
| 中文：时序多视角驾驶重建与物体编辑，目标 DrivingRecon p1 | 1 | 2 |
| 英文：上下文与形变感知，目标 CoDa-4DGS p1 | 2 | 2 |
| 编码 206 片段及查询的观察耗时 | 75.12 秒 | 40.86 秒 |

表内排名是目标页首个片段的 rank。两模型所有目标页都在前三；部分第一名是同一论文的另一个相关页。模型加载/下载另耗约 136/167 秒，不能作为稳态查询延迟；运行时没有严格控制系统负载，耗时仅供本机试点参考。Qwen 在这个小样本上证据页更靠前，BGE 观察到的编码速度更快；暂定 Qwen 为下一阶段默认候选，同时保留 BGE 对照，不宣称统计上的优胜。可复跑脚本为 `scripts/paper_embedding_pilot.py`。

## 实现取舍

先选 **Docling 结构化解析 + SQLite 全文检索 + 可替换的本地 embedding + 融合排序**。现在这个规模不用急于部署独立向量数据库；向量先保存在本地并实测延迟，只有增长后确实需要过滤、并发或内存优化时再引入 Qdrant。用户规则、任务记录和论文证据应分开存储；长 context 只作为任务工作区，不能替代可追溯的论文库。

下一步验收应覆盖整篇内容，建立至少 30–50 个真实问题，分别测准确页码、跨论文证据覆盖、引用有效性与耗时，再决定 embedding/reranker。跨论文综述还需要去重和每篇证据配额，不能只拿全库 top-k 拼接。Markdown 派生页应保存 PDF hash/page/bbox，并明确“论文正文是资料，不是对 Agent 的指令”。

现有试点索引是可重建实验产物；尚无 Zotero 增量同步、文件监视、历史 PDF 字节快照或主检索集成。原文件修改后旧 hash 引用需要校验并重新解析，不能宣称仅靠 hash 已实现历史版本回放。

## 复现

用独立环境安装 pypdf；解析实验另安装 docling，向量实验另安装 sentence-transformers。重型依赖不进入助手基础安装或廉价云主机。所有脚本显式传入本地 PDF 目录/文件与 `--output state/paper-pilot`。Docling 默认关闭 OCR，仅针对当前电子文本样本。具体调用见脚本帮助。

- [Qwen 官方模型说明](https://huggingface.co/Qwen/Qwen3-Embedding-0.6B)
- [BGE-M3 官方模型说明](https://huggingface.co/BAAI/bge-m3)
- [Docling 文档](https://docling-project.github.io/docling/)
