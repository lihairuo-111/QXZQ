# A股情绪周期监控 V3

基于 GitHub Actions + AKShare 的全自动 A 股情绪监控系统。

## 架构

```
GitHub Actions (工作日 16:20 / 17:10 自动运行)
    ↓
updater.py (AKShare 取数 → 计算指标 → 生成 JSON)
    ↓
site/data/*.json (latest / history / status)
    ↓
GitHub Pages 部署 → 用户打开网址查看最新数据
```

## 在线访问

网页地址：https://lihairuo-111.github.io/QXZQ/

## 情绪周期阶段

| 温度区间 | 阶段 |
|---|---|
| 0–20 | 极度恐慌 |
| 20–35 | 情绪冰点 |
| 35–55 | 修复阶段 |
| 55–75 | 活跃加速 |
| 75–100 | 亢奋退潮 |

## 核心指标

- 上涨 / 下跌家数、涨停 / 跌停数
- 炸板率、最高连板
- 昨日涨停今日平均反馈
- 全市场成交活跃度
- 上证指数短期趋势（MA5/MA10/MA20）

## 本地运行

```bash
pip install -r requirements.txt
python updater.py      # 更新数据
```

或双击 `run_update.bat`。

## 文件结构

```
QXZQ
├── .github/workflows/refresh-and-deploy.yml   # 自动化工作流
├── site
│   ├── index.html          # 前端页面
│   └── data
│       ├── latest.json     # 最新数据
│       ├── history.json    # 历史记录(近30天)
│       └── status.json     # 更新状态
├── updater.py              # 数据更新器
├── config.json             # 配置(权重/周期阈值)
├── requirements.txt
├── run_update.bat          # 本地更新数据
└── start_web.bat           # 本地启动网页
```
