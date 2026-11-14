# 标程抽取台（schema-extractor）

**一句话定位：** 从本地归档的 HTML 文件中批量识别表格区域、推断列语义、做规范化抽取，并留下可复现的原始证据、SQLite 台账和抽取质量报告。

**是：**

- 一个配置驱动的 HTML 表格抽取与列语义推断管线。
- 输入是本地目录中的 HTML 归档；仓库自带虚构 fixture，可完全离线运行。
- 输出是结构化记录、原始表格证据、SQLite 台账、`report.md` / `report.json`。
- 纯静态解析，结果确定；同一输入与同一 `--now` 产生相同输出字节。

**不是：**

- 不是爬虫：不联网、不抓取、不排队请求。
- 不是浏览器或 JS 渲染器：不执行脚本，不处理动态 DOM。
- 不是 CRUD 后台、报表看板或通用数据平台。
- 不是 ETL 调度系统：不做 DAG、并发调度、重试、断点续跑。
- 不做列语义的机器学习 / 词向量推断。

## 合规声明

本项目只处理本地目录里的 HTML 归档，运行期不发起任何 HTTP/HTTPS/DNS 请求，也不包含上传、账户或写入型 API。fixture 中的数据全部为虚构演示数据，链接域名只使用 `example-local.invalid` 与 `127.0.0.1` 这类占位值；仓库不包含 token、密钥、真实公司名或真实域名。测试通过 monkeypatch 禁用 `socket` 与 `urllib.request.urlopen`，验证完整管线仍可运行。

## 快速上手

依赖：Python 3.10+、Flask 3.x、PyYAML 6.x、pytest 8.x。若环境已经预装这些依赖，可直接运行；否则执行 `pip install -e ".[test]"`。

```bash
# 1. 运行全部测试
pytest -q

# 2. 执行一次完整抽取（固定时间戳，保证输出可复现）
python -m schema_extractor.cli run \
  --config configs/default.yaml \
  --now 2026-01-01T00:00:00Z

# 3. 启动只读本地查看服务（仅绑定 127.0.0.1）
python -m schema_extractor.cli serve --config configs/default.yaml --port 5188
```

浏览器打开 `http://127.0.0.1:5188/`。也可以执行 `scripts/run_all.sh`，脚本会依次跑测试、一次抽取、一次服务冒烟检查并关闭服务。

其他命令：

```bash
python -m schema_extractor.cli list-runs --config configs/default.yaml
python -m schema_extractor.cli show --config configs/default.yaml --run-id <run_id>
python -m schema_extractor.cli run --config configs/default.yaml \
  --now 2026-01-02T00:00:00Z --diff-with <baseline_run_id>
```

## 配置项说明

路径类配置均相对**当前工作目录**。配置会与内置默认值深合并，未知键或类型错误会直接报错。

| Key | 默认值 | 作用 |
|---|---|---|
| `project_name` | `标程抽取台` | 报告与查看服务使用的项目名。 |
| `input_dir` | `fixtures/inputs` | 本地 HTML 归档目录。 |
| `file_glob` | `*.html` | 输入文件匹配模式，按文件名排序处理。 |
| `output_dir` | `build/default` | 生成物总目录（用于组织，不自动清理）。 |
| `database` | `build/default/runs.sqlite3` | SQLite 台账路径。 |
| `evidence_dir` | `build/default/evidence` | 原始表格 HTML 片段目录，文件按 SHA-256 命名。 |
| `evidence_index` | `build/default/evidence.json` | `hash → {source_file,start,end}` 证据索引。 |
| `report_md` | `build/default/report.md` | Markdown 质量报告路径。 |
| `report_json` | `build/default/report.json` | JSON 质量报告路径。 |
| `table_selector.min_rows` | `1` | 最少数据行数（不含表头）。 |
| `table_selector.min_cols` | `2` | 最少列数。 |
| `table_selector.require_header` | `true` | 是否丢弃无法判定表头的表格。 |
| `table_selector.header_hint_regex` | 中文表头提示正则 | 无 `thead`/`th` 时，匹配首行以判定表头。 |
| `table_selector.nested_table_policy` | `skip` | 嵌套表策略：`skip` 忽略内层；`flatten` 将内层作为独立表处理。 |
| `inference.min_match_ratio` | `0.75` | 列类型被采信所需的最低匹配比例。 |
| `inference.min_non_empty_ratio` | `0.5` | 非空值占比低于此值时回退为 `text`。 |
| `inference.max_enum_cardinality` | `8` | `enum` 允许的最大不同值数。 |
| `inference.max_sample_values` | `3` | 每列保存的样例值数量。 |
| `inference.identifier_pattern` | 字母前缀编号正则 | `identifier` 类型的整值匹配规则。 |
| `normalization.date_formats` | `%Y-%m-%d`、`%Y/%m/%d`、`%Y年%m月%d日` | 日期解析尝试顺序。 |
| `column_overrides` | 缺省不存在 | 可选的列覆盖规则列表，见下节。 |

### 列覆盖规则（`column_overrides`）

自动推断之外，维护者可以钉住个别列的名字或类型。规则段**可选**：不写、
写空列表，或规则一条都没命中时，records、columns、issues 与摘要输出都与
未引入该功能时逐字节一致；`strict.yaml` 也不会继承 `default.yaml` 的规则。

规则在**列类型推断完成之后、逐行构造记录之前**生效。每条规则以三者组合
命中一列（AND 关系，都省略时即匹配所有表的所有列）：

| 字段 | 必填 | 含义 |
|---|---|---|
| `source_glob` | 否，默认 `*` | 相对输入目录的路径 glob，大小写敏感，如 `page_04_messy.html`、`reports/*.html`。 |
| `column_name_regex` | 二者至少一 | 对**推断后的列名**做 `re.search`；需要精确匹配请自行加 `^` / `$`。 |
| `column_index` | 二者至少一 | 0 基列序，与 SQLite `columns.col_index` 一致。 |
| `name` | `name`/`type` 至少一 | 强制列名。 |
| `type` | `name`/`type` 至少一 | 强制类型，取值为 `identifier`、`currency`、`percent`、`integer`、`decimal`、`date`、`enum`、`text`。 |
| `priority` | 否，默认 `0` | 冲突消解权重，见下。 |

**冲突消解（固定语义）：** 列名与类型分别消解——只声明 `name` 的规则不
影响类型，只声明 `type` 的规则不影响列名。每项竞争只看命中的规则，取
`(priority, 配置出现顺序)` 最大者：`priority` 数值大者优先，同优先级时
**后写的规则胜出**。若覆盖后的名字与别的列重名，覆盖名优先占用，其余列从
左到右依次追加 `_2`、`_3`。覆盖后的列名会同时出现在 `records.values` 的
键、`columns` 台账、`issues.col_name` 与报告的 `tables[].columns` 中；
一条列在任何输出里都只有一个名字。

**强制类型与失败路径：** 覆盖不会改变逐格归一化的行为——按新类型归一化
失败的格子保留清理后的原文，并照常产生 `number_unparsable` /
`date_unparsable` 等 issue，`col_name` 使用覆盖后的列名。台账与报告里的
`match_ratio` 会按强制类型对全部非空格重新计算（`text`/`enum` 为 1.0），
使偏低的命中率如实反映「这是人工钉住的列」。

**命中诊断：** 只要配置了规则，`report.json` / `report.md` 会多出
`column_overrides` 段，逐条列出命中列数与生效列数（用于解释优先级落选），
并在 `pipeline.override_warnings()` 中给出「未命中任何列」的过期规则提示。
未配置规则时该段完全不存在。

```yaml
column_overrides:
  - source_glob: "page_04_messy.html"   # 表头丢失的列按列序钉名
    column_index: 2
    name: "备注"
  - source_glob: "page_*.html"          # 金额列被误判成 integer，钉成 currency
    column_name_regex: "^金额"
    type: currency
    priority: 10
```

`configs/strict.yaml` 是更严格的示例：至少 2 行、至少 3 列，类型匹配阈值提高到 0.9。

## 目录结构

```text
.
├── README.md
├── pyproject.toml
├── configs/
│   ├── default.yaml
│   └── strict.yaml
├── fixtures/
│   ├── inputs/                 # 6 个虚构 HTML 归档
│   └── expected/               # 2 份 records.jsonl + 1 份 summary.golden.json
├── scripts/run_all.sh
├── src/schema_extractor/
│   ├── cli.py                  # run / serve / show / list-runs
│   ├── config.py               # YAML 合并、校验、稳定哈希
│   ├── dom.py                  # html.parser 轻量 DOM
│   ├── tables.py               # 表格识别、表头、span 网格展开
│   ├── header_infer.py         # 确定性列类型推断
│   ├── normalize.py            # 数字/日期/文本规范化
│   ├── pipeline.py             # 编排、run_id、输出
│   ├── processing.py           # 表格 → 记录 / issue / 证据
│   ├── models.py               # 运行期结果模型
│   ├── storage.py              # SQLite schema、UPSERT、查询
│   ├── evidence.py             # 证据切片、hash、复现校验
│   ├── report.py               # 质量报告与 diff
│   └── server.py               # Flask 只读本地服务
└── tests/                      # pytest，全部离线
```

生成物 `runs.sqlite3`、`evidence/`、`report.md`、`report.json`、`build/` 均写入 `.gitignore`，不进入交付。

## 数据模型

SQLite 使用 5 张表，schema 内联在 `storage.py` 的 `SCHEMA` 常量中：

| 表 | 主键 | 字段 |
|---|---|---|
| `runs` | `run_id` | `started_at`, `finished_at`, `config_sha256`, `files_scanned`, `records_written`, `issues_count` |
| `tables` | `(run_id, source_file, table_index)` | `evidence_hash`, `column_count`, `row_count` |
| `columns` | `(run_id, source_file, table_index, col_index)` | `name`, `type`, `match_ratio` |
| `records` | `(run_id, source_file, table_index, row_index)` | `data_json`（包含 `values` 与可选 `units`） |
| `issues` | `(run_id, source_file, table_index, row_index, col_name)` | `reason` |

写入统一使用 `INSERT OR REPLACE`。只要 `run_id` 相同，重复执行不会造成行数膨胀。

单条记录的形状（`records.jsonl` 与 `data_json` 同源）：

```json
{
  "source_file": "page_01.html",
  "table_index": 0,
  "row_index": 0,
  "values": {"项目编号": "A-1001", "合同金额": "1234.5"},
  "units": {"合同金额": "CNY"}
}
```

- 数字统一为无千分位、无多余尾零的十进制字符串。
- 百分比保留数值、单位记为 `%`；货币单位映射为 `CNY` / `USD` / `EUR`。
- 日期统一为 `YYYY-MM-DD`；失败时保留清理后的原文并产生 issue，不抛异常。
- 文本清理首尾空白并合并连续空白；空单元格统一为 `""`。

## 证据与确定性

- 每张被采信的表格保存 `source_file`、`table_index`、起止行号与原始 `<table>…</table>` 子串的 SHA-256。
- `evidence/{hash}.html` 保存原始表格片段；`evidence.json` 提供 hash 到文件与行号的映射。
- 相同输入文件 + 相同配置 + 相同 `--now` ⇒ 相同 `run_id`、记录、报告字节。`run_id` 由时间、配置哈希和文件哈希共同确定。
- `report.json` 生成时使用注入的 `--now` 作为 `generated_at`；`test_cli` / `test_golden` 固定时间进行断言。

## 测试

```bash
pytest -q
```

测试覆盖 DOM 解析、表格识别与 span 展开、列推断、规范化、证据、SQLite 幂等、报告、CLI 子进程、Flask 只读服务和 golden 文件逐行比较。`test_offline_guard.py` 在禁用出站 socket 与 `urlopen` 的条件下跑完整管线。所有测试只写 `tmp_path`。

## 已知限制与不实现清单

- 不提供任何形式的网络采集：无 `requests`、出站 `urllib.request`、`http.client` 调用。
- 不做分布式、多进程、线程池并发。
- 不做列语义的 ML 推断或词向量相似度。
- 不做页面内 JSON-LD / Microdata / RDFa 抽取。
- 不执行 JavaScript，不处理动态 DOM。
- 不实现增量抓取、调度、重试或断点续跑（属于 batchsched 的职责）。
- 不实现用户、角色、权限或登录。
- 不实现 CSV/Excel 导入。
- 不实现 Parquet / Avro 等列式格式导出。
- 不实现写入型 API、上传服务或远程监听。
- 不自动推断合并单元格的跨列业务含义；span 只展开为矩形网格。

## 演进方向

- 增加更多确定性表头模板与多语言日期规则。
- 为证据片段增加可选的逐行定位视图。
- 扩展只读服务的筛选与证据对照页面（仍不提供写接口）。
- 在不引入网络与调度的前提下，支持更多静态文档结构（例如定义列表中的键值对）。
