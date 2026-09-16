# 指定联系人朋友圈同步 ABI v1

本契约用于 Windows/macOS 私有原生核心与 Python 后端之间的边界。原生核心负责在微信进程上下文中调用高层 SNS 用户时间线入口；后端不接触微信协议、TLS、签名或密钥。

独立 `wechat-sns-native` 伴随库复用同一组导出符号，但不依赖 `wechatdb-native` 的 client
指针；该参数在独立库中仅为 ABI 占位。Python 加载器仍传入非空哨兵值，以复用相同的输入与
输出校验逻辑。

## 导出符号

以下符号必须成组提供，缺少任意一个时客户端会拒绝启用该能力：

```c
int32_t wce_wechat_moments_sync_get_capability(
    void *client,
    const wce_wechat_moments_sync_capability_options *options,
    wce_wechat_moments_sync_capability *result);

int32_t wce_wechat_moments_sync_begin(
    void *client,
    const wce_wechat_moments_sync_begin_options *options,
    uint64_t *request_handle);

int32_t wce_wechat_moments_sync_poll(
    void *client,
    const wce_wechat_moments_sync_request_options *options,
    wce_wechat_moments_sync_poll_result *result);

int32_t wce_wechat_moments_sync_cancel(
    void *client,
    const wce_wechat_moments_sync_request_options *options);

int32_t wce_wechat_moments_sync_close(
    void *client,
    const wce_wechat_moments_sync_request_options *options);
```

## 数据结构

所有字符串均为 UTF-8。输入指针只在调用期间有效；固定字符串必须以 NUL 结尾，终止符之后的字节必须为零。结构使用目标平台默认的 64 位 C ABI 对齐。

```c
typedef struct {
    uint32_t struct_size;
    uint32_t flags;                 /* bit 0: allow unverified version */
    const char *account_utf8;
    const char *account_directory_utf8;
    uint64_t operation_nonce;
} wce_wechat_moments_sync_capability_options; /* 32 bytes */

typedef struct {
    uint32_t struct_size;
    uint32_t reason;
    uint32_t platform_supported;
    uint32_t ready;
    uint32_t version_verified;
    uint32_t wechat_process_id;
    char actual_wechat_version[32];
    char adapter_id[64];
} wce_wechat_moments_sync_capability; /* 120 bytes */

typedef struct {
    uint32_t struct_size;
    uint32_t flags;                 /* bit 0: resume; bit 1: allow unverified */
    const char *account_utf8;
    const char *account_directory_utf8;
    const char *target_username_utf8;
    const char *resume_cursor_utf8; /* nullable, opaque, at most 255 bytes */
    uint64_t operation_nonce;
    uint32_t scene;                 /* 1: specified-user timeline */
    uint32_t page_size;             /* 0: native/client default */
} wce_wechat_moments_sync_begin_options; /* 56 bytes */

typedef struct {
    uint32_t struct_size;
    uint32_t reserved;
    uint64_t request_handle;
    uint64_t operation_nonce;
} wce_wechat_moments_sync_request_options; /* 24 bytes */

typedef struct {
    uint32_t struct_size;
    uint32_t state;
    uint32_t reason;
    int32_t terminal_status;
    uint32_t completion_reason;
    uint32_t source_complete;
    uint32_t version_verified;
    uint32_t has_more;
    uint64_t pages_fetched;
    uint64_t posts_observed;
    uint64_t rows_written;
    uint64_t oldest_tid;
    char next_cursor[256];
} wce_wechat_moments_sync_poll_result; /* 320 bytes */
```

`next_cursor` 是微信响应产生的不透明分页标识。`has_more=1` 时它必须非空；后端会持久化该值并在应用重启后作为 `resume_cursor_utf8` 回传。只有服务端响应明确表示结束时才能返回 `source_complete=1`；此时 `has_more` 必须为零。不能根据本地记录数、时间跨度或连续空页推测完成。

## 状态值

- `state`: `1 pending`、`2 running`、`3 succeeded`、`4 failed`、`5 cancelled`、`6 paused`。
- `completion_reason`: `0 none`、`1 source_end`、`2 empty`、`3 visibility_boundary`、`4 access_denied`、`5 cancelled`、`6 interrupted`。
- `reason`: 与 `NativeCoreMomentsReason` 一致，包含登录、账号匹配、版本验证、Hook 定位/验证、权限、数据库写入和超时状态。

## 平台适配约束

- 已验证版本通过固定签名定位高层“指定用户时间线/继续加载”函数。
- 未知版本仅在调用方允许时进行特征扫描；必须同时验证函数结构、参数约束、返回结构以及 `sns.db/WAL` 的目标用户写入。任一验证失败返回 `HOOK_VALIDATION_FAILED`，不得调用候选地址。
- 原生核心只能让官方微信客户端完成网络鉴权、加密、签名、响应解析和数据库写入。
- 微信不兼容时返回能力错误，不得回退到界面点击、通用网络 Hook 或私有协议。
- `cancel` 必须可重复调用；`close` 释放句柄但不删除已写入微信数据库的记录。

当前公开仓库同时包含独立的 `native/wechat-sns-native` 控制库源码。它不依赖
`wechatdb-native` 句柄：Python 会优先选择原生核心中融合的五个符号，缺失时加载独立伴随库。
进程内 Hook 载荷及每个微信版本的已验证函数签名仍需逐版本完成；适配器未通过验证时控制库
会失败即停。正式启用仍受签名、装载、授权、法务及安全审查约束。
