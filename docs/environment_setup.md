# 环境与复现说明

## 已验证环境

分析计算使用 Ubuntu 26.04.1 LTS / aarch64、MySQL 8.4.11 和 Python 3.14.4，Python 依赖位于项目 env/python/。Windows 使用 Excel 查看最终结果。本次 Windows 仓库迁移仅整理文件，未安装或配置 Windows Python、MySQL。

env/requirements.txt 记录直接依赖范围，env/requirements_lock.txt 记录实际安装版本。锁定版本对应已验证的 WSL 环境，其他系统是否提供兼容安装包需要另行确认。系统解释器仅用来创建项目环境，不将项目依赖装入全局环境。

## WSL 环境创建参考

以下命令仅用于需要重建分析环境时，在项目根目录执行。本次文件迁移没有执行安装。

```bash
mkdir -p env/bootstrap env/cache/pip env/tmp
python3 -m venv --without-pip env/python
curl --fail --location https://bootstrap.pypa.io/get-pip.py --output env/bootstrap/get_pip.py
PIP_CACHE_DIR="$PWD/env/cache/pip" TMPDIR="$PWD/env/tmp" env/python/bin/python env/bootstrap/get_pip.py
PIP_CACHE_DIR="$PWD/env/cache/pip" TMPDIR="$PWD/env/tmp" env/python/bin/python -m pip install -r env/requirements_lock.txt
env/python/bin/python -m pip check
```

如系统 Python 已支持 pip，可以直接用 python3 -m venv env/python，再在该解释器内安装清单。依赖、下载缓存和临时文件均留在 env/。

## 数据与数据库

先按照 [数据来源说明](../assets/data_source.md) 下载六份业务 CSV。数据库初始化与建表文件为 src/initialize_database.sql、src/create_tables.sql，默认库名 toy_store_ecommerce。使用本机数据库管理工具执行 SQL，再用具有所需权限的项目账号运行导入与分析程序。

默认连接参数为 127.0.0.1:3306，账号 toy_analyst。月度、渠道、漏斗和商品分析主程序支持 --host、--port、--user、--database 参数；导入和质量检查程序使用文件中的默认连接设置，需要其他连接时先调整这两份程序。口令在终端提示时输入，不写入源码、文档或连接配置，不提交 GitHub。账号建立与口令设置按本机数据库情况处理。

首次在空库执行导入。导入程序是追加写入，已有导入库不要再次执行同一批数据。不要为阅读报告而重建或清空现有库。

## 运行顺序

所有命令在项目根目录用隔离环境执行。四个 review 程序读取上一步生成的审计与结果文件，更新发现 JSON，不生成额外业务报告。

```bash
env/python/bin/python -B src/check_environment.py
env/python/bin/python -B src/import_data.py
env/python/bin/python -B src/check_data_quality.py
env/python/bin/python -B src/monthly_overview.py
env/python/bin/python -B src/review_monthly_results.py
env/python/bin/python -B src/channel_analysis.py
env/python/bin/python -B src/review_channel_results.py
env/python/bin/python -B src/funnel_analysis.py
env/python/bin/python -B src/review_funnel_results.py
env/python/bin/python -B src/product_analysis.py
env/python/bin/python -B src/review_product_results.py
```

主程序按依赖顺序运行，不修改数据库业务记录，导入程序除外。分析脚本保存状态与检查结果，失败时旧 CSV 可能仍存在，应先检查 JSON status。关键口径见 [指标定义](metric_definitions.md)。

需要重建看板时，再执行 src/build_excel_dashboard.py；该步骤覆盖同名工作簿，需在 Excel 内重算、检查布局并保存。

## 离线验证

```bash
env/python/bin/python -B -m unittest discover -s tests -v
env/python/bin/python -B src/validate_project.py
```

离线测试不依赖 MySQL 或原始数据。仓库检查读取已保留的分析报告、发现 JSON、汇总 CSV 与最终 Excel；检查文件引用及指纹，并更新验证记录和交付清单。逐条审计不在仓库内，需要完整复核时重跑分析与 review 程序。
