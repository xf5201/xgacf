# TG Premium Auto-Sell Bot

基于 Debian 13 开发的自动代购机器人。

## 功能特性
- ✅ 双链支持 (TON / USDT-TRC20)
- ✅ 双助记词隔离 (资金更安全)
- ✅ HD 钱包技术 (一人一地址，自动归集)
- ✅ 自动监控支付到账
- ✅ 自动调用 Fragment 合约购买

## 部署说明

1. **安装环境**
   bash
chmod +x setup.sh start.sh
bash setup.sh

2. **配置密钥**
   编辑 `.env` 文件，填入 `BOT_TOKEN`, `TON_MNEMONIC`, `TRON_MNEMONIC`。

3. **启动**
   
bash
bash start.sh

## 免责声明
本代码仅供学习交流。Fragment 合约的 Opcode 需要根据实际情况自行调试，请遵守当地法律法规。
