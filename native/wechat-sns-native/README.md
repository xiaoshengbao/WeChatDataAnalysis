# wechat-sns-native

这是独立于 `wechatdb-native` 的指定联系人朋友圈原生同步核心。它实现应用已经约定的
`capability/begin/poll/cancel/close` C ABI，不依赖数据库 native core 的源码、句柄、授权或
broker 生命周期。

组件分为两层：

1. 本目录构建的控制库负责识别正在运行的微信、校验版本/二进制指纹、选择适配器并通过
   同用户私有 Unix socket 转发异步请求。
2. 未来装载到微信进程内的 Hook 载荷负责定位并调用高层 SNS 用户时间线函数，同时验证
   响应游标和 `sns.db/WAL` 写入。只有载荷完成握手后 capability 才会返回 ready。

版本适配器的进程内接口定义在 `include/wechat_sns_hook_adapter.h`。四项必需验证位缺一不可，
控制层也会再次校验二进制签名和桥接进程身份。

当前已经登记本机 macOS 微信 `4.1.5 (31960)` 的可复现二进制指纹，但状态刻意保持为
`research_required`：尚未确认函数签名时不会尝试调用候选地址，也不会返回假 ready。

## macOS 构建与探测

```sh
make -C native/wechat-sns-native
native/wechat-sns-native/build/wechat-sns-probe
```

本地开发可执行 `make -C native/wechat-sns-native install-python` 将库和适配表放入 Python
包的 native 目录。也可用 `WECHAT_TOOL_SNS_NATIVE_LIBRARY` 和
`WECHAT_SNS_NATIVE_ADAPTERS` 指向自定义产物。

## 适配器晋级规则

- `research_required`：只识别版本，永远不 Hook。
- `candidate`：只允许调用方明确开启实验兼容；三个特征必须在目标二进制中各唯一命中，
  且进程内载荷必须完成参数、返回游标和目标数据库写入验证。
- `verified`：二进制 SHA-256 必须精确匹配，仍需通过进程内载荷握手才可用。

macOS 官方微信启用了 Hardened Runtime 和 App Sandbox。如何把载荷安全、合规地放进官方
进程不是这个控制库可以绕过的；正式启用必须另行完成签名/装载方案和授权、法务审核。
本机 4.1.5 的静态筛查结果和首个适配器的动态验证步骤见
`../../docs/wechat-sns-native-research.md`。
