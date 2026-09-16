# 微信朋友圈 Hook 原生核心：当前研究结论

## 已落地的边界

`native/wechat-sns-native` 是可独立构建的朋友圈控制库，不依赖无法获得源码的
`wechatdb-native`。它已经具备：

- 与后端一致的 `capability/begin/poll/cancel/close` ABI；
- macOS/Windows 微信进程与二进制身份探测；
- SHA-256 + 版本 + 架构适配器选择；
- 带通配符的唯一特征扫描和失败即停；
- macOS 同 UID、0600 Unix socket 和 Windows 主进程 PID 绑定的桥接协议；
- 本地句柄映射、分页结果、取消、关闭及微信退出后的暂停语义；
- 独立 Python 加载器，以及原 native core 缺少融合符号时的自动后备；
- macOS Make、Windows/macOS CMake 和桌面打包入口。

进程内版本适配器使用 `wechat_sns_hook_adapter.h`。只有函数结构、参数、响应游标和目标
数据库写入四项验证全部通过，载荷才允许响应 ready。

## 本机微信 4.1.5 (31960)

- 路径：`/Applications/WeChat.app/Contents/MacOS/WeChat`
- 架构：universal，当前进程为 arm64
- 主程序 SHA-256：
  `20f355ca7269a51fbc29e4b50ec4e74bdcac53f47e93dbc49245231f4f202b6d`
- CodeDirectory：Hardened Runtime (`runtime`)
- Bundle：App Sandbox，Team ID `5A4RE8SF68`
- 全局符号：主程序已剥离

静态元数据中能看到 `sns_data_manager`、`GetRemoteDataItem`、`GetNextPageData`、
`CreateUserSession`、`GetLocalPageData`、`RefreshTop` 和 `mmui::ExtensionSnsPage` 等高层线索。
但这些名称位于自定义常量/反射表中，没有可直接使用的导出符号。能直接交叉引用到的
`SNSImageVerifyRequest` 位于 CDN 媒体校验路径，不是指定用户时间线入口，不能拿来冒充
高层 Hook。

因此该版本当前适配器保持 `research_required`。控制库能够识别真实 PID、版本和适配器，
但返回 `wechat_version_unverified`，不会调用任何候选地址。

## 剩余的真实联调门槛

官方 macOS 微信没有 `get-task-allow` 或禁用 library validation 的权限。普通 Developer ID
应用无法稳定向这个 Hardened Runtime 进程附加调试器或注入自定义动态库。要产出首个真实
适配器，需要在隔离测试账号上选择一种研究环境：

1. 对微信副本重新签名并加入调试/载荷权限，只用于定位和验证；或
2. 先在 Windows 测试机通过受控 DLL 装载完成相同的动态跟踪。

动态验证必须从一次正常的“打开指定联系人朋友圈/继续加载”开始，追踪上述数据管理调用，
确认 `username` 和分页游标参数，记录服务端下一页标识，并同时观察该联系人的
`sns.db-wal` 写入。拿到三个稳定签名后先标记 `candidate`；经过多页、空页、隐私边界、取消和
重启验证后才可晋级 `verified`。

这些步骤不能仅靠静态字符串猜测。猜错函数地址会直接导致微信崩溃，也违反本方案的
“未知版本验证失败立即停止”约束。
