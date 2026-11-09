"""应用包入口。

在这里集中导入 ORM 注册表，保证任何进程（api / worker / scripts）
只要 import 了 app 下的任何模块，Base.metadata 就是完整的。
否则单独运行 worker 或某个脚本时，会出现 NoReferencedTableError（外键目标表未注册）。
"""

import app.core.registry  # noqa: F401,E402
