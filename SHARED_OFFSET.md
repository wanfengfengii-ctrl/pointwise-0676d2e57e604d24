# 共享偏移下的量规定稿

两站使用同一个校准源。中心要在两站关库后选定一个共同参考值，即使实际偏移取许可区间内最不利的值。按 README.md 的入口初始化各库。数据库连接仍由调用者持有，事务入口接收空闲连接。

## 计划与测次

- `plan.registration.PlanSpec` 增加可省略的 `offset_interval`。登记仍用 `register(db, plan)`，`load_plan` 可读回该值。省略或 `None` 表示未登记。两个端点用 V1 有理数输入规则，按下界、上界给出，允许相等。偏移区间属于计划的固定声明，在两个站库及中心库一致登记。同号不同声明拒绝。
- `station.intake.record` 增加可省略的关键字参数 `sensitivity`。沿用 V1 的读数、权重和更正规则。灵敏度使用 V1 有理数输入规则，零可显式登记，省略或 `None` 表示缺证。更正将新灵敏度归于新测次。以旧修订为底稿时，其他成员及其灵敏度照旧。指定冻结修订上的灵敏度随原成员保持不变。
- `issue.publication.issue_robust(db, plan_id, station_id, revision_id)` 返回固定的 V2 `StationBundle`。只使用指定冻结修订的有效成员。计划缺偏移、任一成员缺灵敏度或任一修正读数在许可偏移下可能越过 `[0,10]` 时，V2 发行报 `ValueError`。名义 V1 发行仍按原规则办理。同一合格修订可分别发行 V1 和 V2，重签同一修订的同一版返回原件。

## 站件与接纳

V2 沿用 `StationBundle` 的 JSON 外层和 `to_json()`，`version` 为整数 2。`evidence.offset_interval` 是两个有理数字符串组成的数组，按下界、上界排列，和中心固定计划核对。站件绑定原计划、站点、确切修订及发行身份。条件损失正文的表示由实现决定，须足以在中心保全共享偏移下的精确核算。原测次清单、测次号、逐次灵敏度和私有档位留站。

`center.imports.accept(db, bundle)` 支持 V2 对象及其 JSON 文本，沿用 V1 入口接纳 V1。中心核对声明、出处字段及证据的结构和数值合法性。版本、计划、站点与站件号共同区分接纳身份。同一发行件通过对象或其导出文本重投，返回原接纳号。JSON 空白和对象字段顺序不改变重投结果。同一身份的固定声明或证据内容有冲突则拒绝。`center.catalog.load_accepted(db, acceptance_id)` 按接纳号读取原件。

## 定稿与领取

`report.publication.finalize_robust(db, plan_id, first_acceptance_id, second_acceptance_id)` 返回稳健报告。必须指明确切接纳号，每个适用站各一份 V2，提交次序不改变计划站序。中心只用本库的计划和已接纳站件。对于每个容许偏移 `d`，各测次的修正值为 `value + sensitivity * d`。对一个共同参考值 `t`，两站分别以本次修正值沿用 V1 的档序、参考档锚定、同档约束及加权绝对误差求本地最小损失。每个偏移下允许重新拟合。先在同一个 `t` 和 `d` 上加总，再在计划偏移区间上取最大值。对 `[0,10]` 内的所有 `t`，报告最小的最坏总误差，以及达到它的整个闭区间。

返回值含 `report_id`、`plan_id`、`offset_interval`、`reference_interval`、`minimum_error` 和 `sources`。区间端点及误差均为 `Fraction`，单点用相等端点表示。`sources` 沿用 V1 的字段和计划站序。报告和两份出处同次提交，提交失败不留下部分成果，可在解除故障后重试。`report.retrieval.retrieve_robust(db, report_id)` 在更正和重开后直接领取固定值和出处。V1 与 V2 分别使用对应的定稿和领取入口。保留既有库的 V1 读写、V1 站件编码、已接纳件、历史报告及全部原测试。

非法输入、坏件、声明或身份冲突、错计划、同站及混版选件报 `ValueError`。形状合法的未知编号报 `LookupError`。SQLite 提交错误沿用原异常。

## 样例和核验

`shared_offset_sample.json` 中各 `cases` 独立登记为计划，量规、单位、参考档和站序取文件顶层字段。每个样例的 `readings` 供站端录入，`correction` 在有该字段时从乙站原冻结修订办理。样例的分数文本按精确值比较。

- `HB-sign-change` 用原甲件分别搭配乙站原件及更正件。关闭源库后分别签出 `[4,6]` 和 `[5,5]`，误差均为 `2`。重开中心库后核对两个报告各自的区间和选件出处。
- `HB-interior` 签出 `[9/2,9/2]`，最坏误差为 `4`。`HB-zero-offset` 签出 `[2,8]` 和 `6`，与同修订的 V1 名义定稿一致。
- 核对同一修订双版共存和同版重投。缺灵敏度、修正读数越域、错偏移区间、同身份冲突和混版应按所属入口拒绝。验证 V2 签发遇到提交失败时报告与出处一并回滚，解除故障后可重新签发。原回归 `tests/test_center.py` 中的 `Lab.detach_sources()` 和 `install_commit_failure(db)` 示范了源库移离和提交时触发失败的核验方式，可按 V2 的实际存储接入。

沿 `.pointwise-runtime.json` 接通业务核验和演示。`verify` 运行完整原回归并核对新链路，失败时以非零状态退出。`demo` 通过真实入口演示断源定稿及更正后重开领取，展示参考区间、最坏误差和采用的站件出处。
