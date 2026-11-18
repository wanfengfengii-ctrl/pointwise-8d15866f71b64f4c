# 澄川材料实验室固定拍取样

站长依据班前校准单登记探头和拍表。派单员为每片试片指定探头、拍和整数微米目标。值班员通过登记拍，向设备发送当拍命令，再按触发号接收设备回值。质控员用原样本出处复算偏差。夜班凭交接号领取已经签发的逐件状态和计数。

## 运行

在项目根目录使用 Python 3.11。项目只使用标准库。

```text
python3 -B check_project.py
python3 -B -m unittest discover -s tests -t . -v
python3 -B demo.py
```

正常回归入口由运行环境随项目提供。演示读取 `demo-shift.json`，使用独立临时库和脚本设备。第 1 拍为空，在第 2、9、10 拍发令后先收第 9 拍，再收第 2 拍。第一次交接有合格、越界、待回和未触发各一件。随后补签第 10 拍并再次交接。演示关闭数据库并重开，校验两单及引用的原回执，输出实际结果。

## Python 入口

各写操作自行开始和结束 SQLite 事务。调用方持有 `open_workspace` 返回的连接，在完成操作后关闭连接。

| 入口 | 参数与结果 |
| --- | --- |
| `shift.bootstrap.open_workspace` | `path` 和可选的 `timeout` 秒数。打开数据库并初始化，返回连接。 |
| `shift.bootstrap.initialize_workspace` | `db`。在事务中创建缺失结构。重复初始化保留已有业务记录。 |
| `shift.register.register` | `db, shift_id, slots, calibrations`。`slots` 将正整数拍号映射到允许探头的有限可迭代对象，允许空集合。`calibrations` 将探头映射到 `Calibration(correction_um, tolerance_um)`。返回班号。 |
| `sampling.planner.book` | `db, shift_id, ticket_id, beat, probe, target_um`。预约一个许可的未来拍，返回工单号。 |
| `device.clock.tick` | `db, shift_id, beat`。推进到下一登记拍，有工单时返回 `Command`，空拍返回 `None`。 |
| `device.receive.receive` | `db, trigger_id, raw_um`。返回该触发的固定 `Receipt`。 |
| `inspection.receipts.receipt` | `db, receipt_id`。按号领取固定 `Receipt`。 |
| `handover.publish.publish` | `db, shift_id`。从事务内的一致视图签发并返回新交接号。 |
| `handover.read.get_report` | `db, report_id`。按号返回 `Report`，包含班号、原顺序逐件行和四类计数。 |

```python
from contextlib import closing
from device.clock import tick
from device.receive import receive
from handover.publish import publish
from handover.read import get_report
from sampling.planner import book
from shift.bootstrap import open_workspace
from shift.calibration import Calibration
from shift.register import register

with closing(open_workspace('lab.sqlite3')) as db:
    register(db, 'day', {1: [], 2: ['P']}, {'P': Calibration(2, 3)})
    book(db, 'day', 'coupon-2', 2, 'P', 100)
    tick(db, 'day', 1)
    command = tick(db, 'day', 2)
    result = receive(db, command.trigger_id, 101)
    report_id = publish(db, 'day')
    report = get_report(db, report_id)
```

上例供一个新库运行。重开后可继续按已提交的拍钟驱动，并可按原编号领取成果。`Command` 包含 `trigger_id`、`shift_id`、`ticket_id`、`probe` 和 `beat`。`device.commands.device_payload` 将这些字段转换为设备载荷。设备回复的先后顺序自由。

## 数据与错误

班号、工单号、探头均为非空字符串。工单号全库唯一，班内每拍只有一个预约。班表可有拍号间隔，下一拍由已过拍和班表决定。映射次序和探头许可次序不影响同内容重投。班表发布后固定。班内拍钟、工单、触发、回执和历史交接保存在同一工作库。

读数、目标、修正和公差使用整数微米，公差须非负。修正值为原读数加修正，偏差为修正值减目标，绝对偏差等于公差也合格。`Receipt` 包含原触发出处、`measurement`、`tolerance_um` 和 `verdict`。`measurement` 保留原读数、修正、目标、修正值和有符号偏差。整数按规范十进制文本存取，拍号按数值排序。交接中的 `untriggered` 和 `pending` 行无回执及实测，已检行记为 `passed` 或 `outside` 并引用回执。交接一经签发，后续收值由新的交接体现。

非法参数、拍位或工单冲突、同班号异配置、错误拍序及异值重投报 `ValueError`。未知合法班号、触发号、回执号或交接号报 `LookupError`。同值重投返回原回执。数据库失败传播 `sqlite3.Error`，该次操作回滚。首签失败可按原触发号重提交回值，交接失败可重新发布。公开入口使用连接默认的行返回类型。
