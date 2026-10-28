# 操作与参数

所有实体 ID 由 HA 注册产生，示例使用虚构的 `switch.example_*`。先在设备页确认真实目标，再替换示例。配置操作本身不会自动打开对应预约或人感开关；启用需单独操作。

## 人感场景

四类开关分别对应人感照明、夏日吹风、人感换气、人感除臭除菌。只选择其中一个目标，可修改生效开始/结束、进入延时 0–3600 秒、离开延时 0–1200 秒。

```yaml
action: aupu_cloud.configure_human_scene
target:
  entity_id: switch.example_human_scene
data:
  start: "09:10"
  end: "16:40"
  presence_delay_seconds: 5
  absence_delay_seconds: 30
```

## 感应夜灯

此配置开关与“小夜灯”实际输出实体分开。可跨午夜；星期至少选一天。省略的字段保留。

```yaml
action: aupu_cloud.configure_motion_night_light
target:
  entity_id: switch.example_motion_night_light
data:
  start: "20:10"
  end: "08:20"
  absence_delay_seconds: 30
  repeat_days: [mon, tue, wed, thu, fri, sat, sun]
```

## 预约

| 功能 | 时长上限 | 可额外设置 |
| --- | --- | --- |
| 换气 1/2/3/4/5 档 | 480/300/180/60/30 分钟 | 风档 1–5 |
| 暖房 | 90 分钟 | 目标温度 30–42℃ |
| 恒温干燥 | 120 分钟 | 无 |
| 除臭除菌 | 60 分钟 | 无 |

可选时长为 10–60 分钟每 10 分钟一级，之后每 30 分钟一级，并受上表限制。`repeat_days: []` 表示不重复；省略则保留。一次性执行和设备端的重新启用行为以官方设备规则为准。

```yaml
action: aupu_cloud.configure_schedule
target:
  entity_id: switch.example_ventilation_schedule
data:
  start: "08:20"
  repeat_days: [mon, wed, fri]
  fan_speed: 5
  duration_minutes: 20
```

换档时保留实际分钟数。如果旧预约 120 分钟而新档最多 30 分钟，必须同时填写兼容时长，否则拒绝修改；不会静默截断。新配置保留开关、其他预约和未解释字节。

## 排错

| 现象 | 检查 |
| --- | --- |
| 未发现设备 | 账户归属、有效令牌、产品 ID 是否为 10363；不要绕过型号限制 |
| invalid_auth | 令牌或 MQTT 凭据需要重新授权；目前没有自动续期 |
| icmp_unreachable | 从 HA 所在主机/容器检查该设备可达性与 ICMP 权限 |
| neighbor_missing | HA 不能直接读取邻居 MAC；常见于跨子网或容器网络边界 |
| neighbor_mismatch | DHCP 地址发生变化或填写了其他设备；核实固定地址与真实 MAC |
| cannot_verify_mqtt | 分别核对局域网核验、互联网及云凭据；不要反复发送控制来测试 |
| 控制超时 | 结果未确认。先查看官方应用和设备实际状态，不能直接认定未执行 |
| 设备温度不可用 | 无效字段、缺少故障状态或 E1 探头故障；不会用设温值替代测温 |

协议未知、云端接口变化或新型号差异需要代码复核，不能靠关闭校验解决。
