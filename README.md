# 衡桥双站量规标定

衡桥计量试验室的两个站点按同一口径复测量规。站员需要保留同档的独立测次，中心质控需要在站库离线时核账，接班复核员需要在更正后按旧号领回原结论。系统使用 Python 3.11 标准库和 SQLite。

## 一次交班

1. 质控员把同一计划登记到两个站库及中心库，固定量规、单位、参考档和适用站点。
2. 站员按档录入具名复测。例中甲站在参考档下方测得 8，乙站在上方测得 2，权重各为 1。
3. 两站分别冻结队列。发行返回带原测次出处的本站核查和供中心接纳的固定站件。
4. 中心验件并固定接纳号，随后关闭两站源库。中心直接从自己的数据库取件。
5. 质控员明确选择两件并签发报告。接班时凭报告号领取共同参考值区间、最小总误差和两站出处。
6. 甲站从原修订更正权重为 2，再冻结和发行。中心接纳甲站新件，继续选用乙站原件，再次定稿。
7. 重开中心库，分别领回两次报告并按旧、新顺序对照。报告各自绑定当时选件，变更出处指向甲站。

## 精确核账口径

档号是有序整数，允许负数、空档和同档多条独立测次。录入顺序不决定档序。数值输入接受整数、`Fraction` 或整数及分子/分母文本。浮点数和缺失数值拒收。读数、拟合值和参考值的闭域为 `[0,10]`，权重严格为正。所有读数使用计划单位，误差按各测次权重加权。

固定参考值 `t` 后，各档拟合值随档位非降，参考档拟合值为 `t`。同档各测次使用同一个拟合值，并各计一次加权绝对差。参考档测次直接相对 `t` 计差。没有测次的一侧贡献为零。本站最小误差是满足这些条件时的最小加权绝对差总和。中心在同一个 `t` 上把两个本站最小误差相加，取其最小值和所有最优参考值构成的闭区间。

V1 站件以 JSON 交接，外层固定版本、计划口径、站点、修订号及本站件号。证据以闭域上的精确分段线性损失交接，分数用字符串表示。站件不携带测次表或私有档位表。中心核验口径、出处字段和损失合法性，实测与损失的核查可在站内按修订办理。站件号是本站库内的发行身份，中心按计划、站点、站件号固定接纳身份。

## 公开 Python 入口

数据库连接由调用者打开并关闭。事务入口接收空闲的 `sqlite3.Connection`，并拥有各自的事务。在站库调用 `station.initialize_station(db)`，在中心库调用 `center.initialize_center(db)`。初始化可重复运行。各站部署使用独立源库，中心使用独立库。表结构初始化保留已有的测次、队列、修订、接纳和报告。

| 入口 | 调用及结果 |
| --- | --- |
| `plan.registration.register` | `register(db, PlanSpec(plan_id, instrument, unit, ReferenceSpec(position), stations))` 固定计划并返回计划号。`ReferenceSpec` 来自 `plan.reference`。相同登记取回原号。 |
| `station.intake.record` | `record(db, plan_id, station_id, reading_id, position, value, weight)` 登记新测次。更正同时指定 `base_revision_id=` 和 `replaces=`。 |
| `station.revisions.freeze_revision` | `freeze_revision(db, plan_id, station_id)` 固定当前有效成员，返回带 `revision_id` 的修订。`load_revision(db, revision_id)` 恢复原集合。 |
| `issue.publication.issue` | `issue(db, plan_id, station_id, revision_id)` 返回 `(LocalCheck, StationBundle)`。同一修订重签取得同一固定件。`StationBundle.to_json()` 导出固定件。 |
| `center.imports.accept` | `accept(db, bundle)` 接收站件对象或 JSON 文本，返回接纳号。相同原件重复接纳时返回原接纳号。 |
| `report.publication.finalize` | `finalize(db, plan_id, first_acceptance_id, second_acceptance_id)` 签发 `SignedReport`，含 `report_id`、`reference_interval`、`minimum_error` 和 `sources`。出处按计划站序排列。 |
| `report.retrieval.retrieve` | `retrieve(db, report_id)` 领回已提交的精确结果及出处。 |
| `report.comparison.compare` | `compare(db, old_report_id, new_report_id)` 返回固定的 `old`、`new` 和按站对齐的 `changed_sources`。 |

`station.ledger.load_draft` 可核对当前队列。办理更正时以所选冻结修订作为底稿，只替换其中指定的一条测次；同档其他测次仍有效。新测次必须使用新的测次名。按号核对本站件和中心接纳件可分别调用 `issue.artifacts.load_station_bundle` 和 `center.catalog.load_accepted`。

无效输入、口径冲突、无效更正或不合法选件使用 `ValueError`；未知计划、修订、站件、接纳或报告号使用 `LookupError`。数据库提交错误由 SQLite 异常报告，报告主体和来源同时回滚。更正不会改写旧修订和旧站件。领取及对照只读固定快照。

## V2 共享偏移稳健链路（见 SHARED_OFFSET.md）

- 计划可在 `PlanSpec(..., offset_interval=(下界, 上界))` 固定有理闭偏移区间，允许单点（两端相等）；三库一致登记，同号不同声明拒绝。
- `station.intake.record(..., sensitivity=...)` 为新测次登记有理灵敏度，零可显式登记，省略即缺证；更正把新灵敏度归于新测次，旧成员灵敏度照旧。
- `issue.publication.issue_robust(db, plan_id, station_id, revision_id)` 按确切冻结修订发行整数版本号 2 的 `StationBundle`；计划缺偏移、成员缺灵敏度或任一修正读数 `value + sensitivity*d` 在端点越出 `[0,10]` 时拒发。同一修订的 V1/V2 可共存，同版重签取回原件。
- V2 证据 `evidence.offset_interval` 为两个有理数字符串，另含两个偏移端点上的精确分段线性本地损失；测次清单、测次号、灵敏度和私有档位不离开站库。
- `center.imports.accept` 同时接纳 V1/V2 对象或 JSON 文本；身份按版本、计划、站点、站件号区分，同件重投（含空白与字段顺序变化）取回原接纳号，冲突报 `ValueError`。
- `report.publication.finalize_robust(db, plan_id, first_acceptance_id, second_acceptance_id)` 在同一参考值 `t` 和同一偏移 `d` 上先合计两站损失，再在偏移闭区间取最坏值，输出最坏总误差最低的完整闭区间及精确 `Fraction`；两份出处与报告同次原子提交。`report.retrieval.retrieve_robust(db, report_id)` 按号领取。混版、同站、错计划选件报 `ValueError`，形状合法的未知号报 `LookupError`。

## 运行

```sh
python3 -B check_project.py
python3 -B demo.py
```

`check_project.py` 由项目运行环境在物化时提供。原测试包也可直接以 `python3 -B -m unittest discover -s tests -t . -v` 运行。测试和演示各自使用临时 SQLite 数据库。`sample.json` 给出演示使用的固定计划、原测次和甲站更正。演示通过真实入口核对等权报告 `[2,8]`、更正后报告 `[8,8]`、两次最小总误差 6 以及甲站换件。
