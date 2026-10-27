# 项目目录结构规范

本文件定义 `mnjy` 的文件放置规则。**新增文件必须先按本规则归位，再提交/交付**；根目录不允许堆放零散文件。

## 目录总览

```
mnjy/
├─ app.py               # HTTP 服务入口（后端唯一代码文件，固定在根目录）
├─ .gitignore
├─ docs/                # 所有文档与设计归档
│  ├─ PRODUCT_SPEC.md   # 产品文档（§13 为 as-built 现状）
│  ├─ README.md         # 启动与测试说明
│  ├─ STRUCTURE.md      # 本文件：目录与放置规则
│  └─ prototypes/       # 设计原型（不参与服务）
├─ scripts/             # 运维/批处理脚本
│  └─ daily_close.py    # 收盘快照任务（Windows 计划任务 mnjy-daily-close 调用）
├─ static/              # 前端资源（由服务端直接提供）
│  ├─ index.html
│  ├─ app.js
│  └─ styles.css
├─ tests/               # 自动化测试（unittest）
│  └─ test_app.py
└─ data/                # 运行时数据（gitignore：账号库 SQLite、server.log、daily_close.log）
```

## 放置规则

| 文件类型 | 放置位置 | 说明 |
|---|---|---|
| 服务端业务代码 | 根 `app.py` | 单入口；`python app.py` 为既定启动方式，测试按模块名 `app` patch。若将来拆分模块，新建 `app/` 包并保留根 `app.py` 作为薄入口，启动命令不变 |
| 运维 / 一次性脚本 | `scripts/` | 从项目根执行：`python scripts/<name>.py`；需要 `import app` 时先 `sys.path.insert(0, 项目根)`（参考 `scripts/daily_close.py`） |
| 前端页面/脚本/样式 | `static/` | 只放被服务端直接提供的文件；设计稿、原型不放这里 |
| 测试 | `tests/` | `python -m unittest discover -s tests` |
| 产品文档 / 说明 / 规范 | `docs/` | 所有 `.md` 一律放这里，根目录不再放文档 |
| 设计原型 | `docs/prototypes/` | 纯静态归档，不参与服务 |
| 运行时数据 | `data/` | 账号库、日志；禁止放代码、文档或素材 |

## 约定

- 根目录只允许出现：`app.py`、`.gitignore` 与 `docs/ static/ tests/ scripts/ data/` 五个目录。
- **移动 `scripts/daily_close.py` 必须同步更新 Windows 计划任务 `mnjy-daily-close` 的动作路径**（当前为 `D:\project\mnjy\scripts\daily_close.py`），否则 16:30 收盘快照会静默失效。
- 命名：Python 模块/脚本用 snake_case；文档用大写+下划线（`PRODUCT_SPEC.md`）或约定俗成的 `README.md`；前端沿用 `index.html` / `app.js` / `styles.css`。
- 生成物（`__pycache__/`、`.pytest_cache/`）已被 `.gitignore` 忽略，不要提交，也不要手工复制到项目里。
- 临时产物（截图、导出、试算脚本）不要落在项目目录；需要长期保留的脚本放 `scripts/`，需要长期保留的说明放 `docs/`。
