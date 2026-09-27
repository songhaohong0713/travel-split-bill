# DeepSeek 小票视觉识别接入

## 目标

将小票识别从 CloudBase 托管视觉模型切换为 DeepSeek 官方视觉模型。用户上传 JPEG 小票后，系统提取日文或英文商品原文、中文翻译、金额和 ISO 货币代码，继续进入现有的确认与编辑流程。

## 范围

- 保持 `POST /v1/receipt-jobs`、小程序上传流程、数据库结构和候选条目 JSON 格式不变。
- 后端使用云托管环境变量 `DEEPSEEK_API_KEY` 和可选的 `DEEPSEEK_MODEL`（默认 `deepseek-flash`）。
- 后端将 JPEG 临时编码为 Data URL，并通过 DeepSeek OpenAI 兼容接口发送一次图片理解请求。
- 保留现有三次重试和“识别失败可手动录入”的用户体验。

## 非目标

- 不配置 CloudBase AI 控制台，不增加腾讯 OCR，也不增加第二次翻译调用。
- 不把 DeepSeek Key 放入小程序、请求参数、Git 或日志。
- 不改变旅行、消费、分账和协作邀请功能。

## 设计

新增一个最小的 DeepSeek 小票提供方，复用现有候选条目 JSON 解析与校验逻辑。应用启动时仅在存在 `DEEPSEEK_API_KEY` 时挂载该提供方；缺失密钥时保持现有失败提示。

请求使用 `https://api.deepseek.com/v1/chat/completions`，认证头为服务端环境变量，模型名来自 `DEEPSEEK_MODEL` 或默认值。提示词要求只返回严格 JSON：

```json
{"items":[{"source_text":"","translated_text":"","amount":"","currency":""}]}
```

无效模型响应、网络错误、非 200 响应和空候选条目均统一映射为既有识别失败路径；不会泄露供应商响应或密钥。

## 验证

- 单元测试断言环境变量读取、目标 URL、Bearer 认证、模型名与视觉请求体。
- 单元测试断言有效 JSON 可转换为现有候选条目，以及供应商错误被安全转换。
- 运行完整后端测试与静态检查。
- 部署后由用户上传一张测试小票进行真实连通性验证。
