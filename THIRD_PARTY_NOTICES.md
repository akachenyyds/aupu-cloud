# 依赖与第三方说明

本仓库不内嵌下列依赖的源码、wheel、虚拟环境或厂商应用资源；HA 或开发环境通过包管理器独立安装。根目录 MIT 许可只覆盖本项目自写部分，不替换第三方许可。

| 组件 | 版本/来源 | 用途 | 许可 |
| --- | --- | --- | --- |
| paho-mqtt | `2.1.0`，HA manifest 固定 | MQTT over TLS/WebSocket | `EPL-2.0 OR BSD-3-Clause`；以该版本分发包声明为准 |
| icmplib | `3.0.4`，HA manifest 固定 | Linux ICMP 在线检查 | GNU LGPLv3；以该版本 LICENSE 为准 |
| Home Assistant Core | 完整测试固定 `2026.9.4` | 宿主、配置流程、实体与协调器 API | Apache-2.0 |
| voluptuous | 由宿主 HA 提供，不额外固定 | 配置和操作参数校验 | BSD-3-Clause |
| Python 标准库 | 由宿主提供 | TLS、HTTP、并发、编解码和局域网工具调用 | Python 自身许可 |

测试环境通过 HA 安装其完整依赖；`requirements.txt` 只列组件直接安装的两个依赖，与 `manifest.json` 同步。完整环境版本清单及直接依赖漏洞查询结果保存在 `docs/audit/`。这不代表所有传递依赖已逐一做许可证兼容审查或漏洞检查。

开发审计工具 `pip-audit` 单独列于 `requirements-audit.txt`，不随 HA 组件安装，项目不内嵌其实现。

GitHub CI 使用官方 `actions/checkout` 和 `actions/setup-python`，均以完整提交摘要固定，许可均为 MIT；仅在 GitHub 工作流执行时拉取，不随源码包内嵌。许可证：[checkout 固定提交](https://github.com/actions/checkout/blob/11d5960a326750d5838078e36cf38b85af677262/LICENSE)、[setup-python 固定提交](https://github.com/actions/setup-python/blob/ece7cb06caefa5fff74198d8649806c4678c61a1/LICENSE)。当前尚未运行远端 CI。

## 核对来源

- [paho-mqtt 2.1.0：PyPI](https://pypi.org/project/paho-mqtt/2.1.0/)；[固定版本许可证声明](https://github.com/eclipse-paho/paho.mqtt.python/blob/v2.1.0/LICENSE.txt)。
- [icmplib 3.0.4：PyPI](https://pypi.org/project/icmplib/3.0.4/)；[固定版本许可证](https://github.com/ValentinBELYN/icmplib/blob/v3.0.4/LICENSE)。
- [Home Assistant 固定版本许可](https://github.com/home-assistant/core/blob/2026.9.4/LICENSE.md)。
- [voluptuous 源项目](https://github.com/alecthomas/voluptuous)。
- [Python 许可](https://docs.python.org/3/license.html)。

如果日后重新分发依赖本体，必须一并处理其版权通知、许可文本及适用的源码提供义务，不能仅保留本仓库的 MIT 文件。当前采用不内嵌依赖的源码分发方式。

## 名称与协议

AUPU/奥普、Q360T5-Pro、Home Assistant、AWS 等名称仅用于说明兼容性。云 API 域名、产品 ID、属性编号、状态值和协议布局为互操作所需信息；保留这些信息不表示厂商授权或官方合作。厂商应用、小程序源码、图标和截图不在本仓库中。
