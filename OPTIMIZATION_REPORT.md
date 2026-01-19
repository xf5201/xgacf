# 项目优化笔记

### 1. 安全
*   **OkPay 验签**: `web_app.py` 加了 MD5 签名校验，防假回调。
*   **IP 过滤**: 加了 `OKPAY_ALLOWED_IPS` 白名单，只认指定 IP。

### 2. 代码结构
*   **模板分离**: 支付成功页 `payment_success.html` 单独拿出来了，不写死在代码里。
*   **日志清理**: 去掉了一堆没用的 debug 日志，看着清爽多了。

### 3. 性能和 Bug
*   **DB 索引**: `orders` 表的 `user_id`, `order_id`, `ton_addr` 加了索引，查起来快点。
*   **Bug 修复**: `okpay.py` 里有个变量没定义的 bug 修了，还有几个缩进和引号的语法问题也改了。
*   **依赖锁定**: `requirements.txt` 加了版本范围，省得库一更新就崩。

### 4. 待办
*   上线前记得把 `.env` 里的 Token 和 Secret 换成真的。
*   要是用户量大了，最好把 SQLite 换成 PostgreSQL 或者 MySQL。
