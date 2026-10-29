# 模型绑定证据（2026-10-25）

这份草稿用于审核，不允许调用。A=官方公开文档足以确定的字段；B=资料缺证或需要技术处理；C=另行授权后才能核查的账户/观测行为。文档、代码门槛、账户能力、观测结果分别记录。所有模型保持 NOT_BOUND，live_authorized=false，billing.complete=false、verified_upper_bound=false、request_limits.verified=false。

## GLM：L2、L3 A、L4

| 字段 | 结论与配置动作 | 状态/来源 |
| --- | --- | --- |
| model/base_url | 保持 glm-4.6v、open.bigmodel.cn/api/paas/v4，Chat Completions | A：[模型页](https://docs.bigmodel.cn/cn/guide/models/vlm/glm-4.6v)、[API](https://docs.bigmodel.cn/api-reference/模型-api/对话补全) |
| parameters.thinking / max_tokens | disabled 有文档支持；保留 8192。具体模型最大 max_tokens=32768，不误用文本 GLM-4.6 的 131072 | A：[核心参数表](https://docs.bigmodel.cn/cn/guide/start/concept-param) |
| tariff | 官方 [0,32K) 价格 1/3/0.2，[32K,128K) 价格 2/6/0.4，顺序为输入/输出/缓存，单位元/百万。缓存存储当前限时免费。未找到 K 的整数定义，不能猜成 1000 或 1024；tiers 保留空列表，原 pricing_reference 留存参考 | 价格 A，整数边界 B：[价格](https://docs.bigmodel.cn/cn/guide/start/pricing) |
| context_tokens | 官方仅标 128K；数值保持 null，不由其他模型/国际站推导 | B：[模型页](https://docs.bigmodel.cn/cn/guide/models/vlm/glm-4.6v) |
| usage_contract / reasoning | [缓存指南](https://docs.bigmodel.cn/cn/guide/capabilities/cache)在示例提示词中提及 cached_tokens 和推理计入输出，但示例材料不作为 GLM-4.6V 正式字段契约；专用路径、缺失语义、图像/推理总量仍未确认 | B；有限调用仅能观察 C |
| 图像、请求体、token 封装上界 | 当前读取的具体模型页面未提供足够的可验证完整上界；相关字段保留 null/空映射 | B |
| output 费用分量 | 8192×6/百万=0.049152，采用已公布两档最大输出价；仅是条件算术，不表示完整请求预留已经证明 | A 算术，整体 B |

## Doubao：L3 B

| 字段 | 结论与配置动作 | 状态/来源 |
| --- | --- | --- |
| model/base_url/thinking | 保持 doubao-seed-2-1-pro-260915、北京 Ark v3；支持 disabled；max_completion_tokens=8192 包含回答与思考 | A：[模型参数](https://docs.volcengine.com/docs/ark/model-parameter-support?lang=en)、[Chat API](https://console.volcengine.com/ark/region:cn-beijing/docs/chat-api) |
| tariff | 常规在线、非音频输入6/输出30/缓存1.2。价格表明确单位“千 token”，[0,1024] 包含右端点，因此转换为整数半开 [0,1024001)。这是价格表单位的十进制转换，不是把模型列表的 1024k 上下文自行换算 | A：[价格](https://console.volcengine.com/ark/region:cn-beijing/docs/model-pricing) |
| context | 模型列表标1024k，未用其他资料猜测其单位；context_tokens=null。65536 是沿用 L3 协议的本地输入保护值，不声称它是供应商最大输入 | B：[模型列表](https://console.volcengine.com/ark/region:cn-beijing/docs/model-list) |
| cached_input_path / missing_cache | 填 prompt_tokens_details.cached_tokens；无缺失即零的保证，因此明确 unknown。null 与字段缺失不同：现代码对返回 null 会报无效缓存用量，不能当成零 | A 路径和保守策略：[Chat API](https://console.volcengine.com/ark/region:cn-beijing/docs/chat-api) |
| reasoning / images | reasoning_in_output=true；图像作为输入 token。usage_contract 整体仍未 verified，标准请求是否绝无独立费用尚待闭合；不使用显式缓存创建/存储 | A 维度，整体 B：[Chat API](https://console.volcengine.com/ark/region:cn-beijing/docs/chat-api) |
| image_tokens_by_detail | 官方 Seed 2.0 及以后：low上界1280、high1280、xhigh5120；省略 detail 默认 high。代码内部把省略键名记为 auto，所以填 auto=1280，但不声称供应商接受显式 auto | A 文档候选：[图片理解](https://docs.volcengine.com/docs/ark/image-understanding?lang=zh) |
| 图像/体积 | 填36000000像素、原始及base64各10000000字节、请求64000000字节。MB未给二进制定义时使用较小十进制本地限值，明确不是断言 MB=MiB。数量无独立确定值，max_images=null，不把页面读取额度当图片重发数 | A保守本地限值，数量 B：[图片理解](https://docs.volcengine.com/docs/ark/image-understanding?lang=zh) |

图片理解页面直接打开仅返回 JS 壳，以上依据来自该官方URL本轮搜索返回的完整正文段落；不是第三方转载。存在可复核性限制，整体 request_limits.verified 保持 false。文档还要求宽高>14和宽高比<=150；当前代码只校验像素乘积，不能认证所有任意形状图片。现有代表样本另做尺寸核查。

## Qwen：L3 C

| 字段 | 结论与配置动作 | 状态/来源 |
| --- | --- | --- |
| model/region | 保持 qwen3.8-max-0902，北京标准在线；snapshot 在官方模型页列明 | A：[模型](https://help.aliyun.com/en/model-studio/qwen3-8-max) |
| tariff/context | 输入12/输出36/隐式缓存1.5元每百万；不使用显式缓存。最大非思考输入991808，context=1000000，最大输出131072；适用范围截成 [0,991809)，不把价格延伸到模型不可接受区间。max_input_tokens=65536是沿用协议保护值 | A：[模型](https://help.aliyun.com/en/model-studio/qwen3-8-max) |
| endpoint | 保留旧北京 dashscope 域名；官方说明旧域名仍可使用，迁移不是本轮必修。账户是否开通该模型仍 C | A：[Chat API](https://help.aliyun.com/zh/model-studio/qwen-api-via-openai-chat-completions/) |
| thinking/max_tokens | hybrid 模式支持 enable_thinking=false；保留 max_tokens=8192。关闭思考时它限制回答长度。不要自行换成 max_completion_tokens：后者文档允许最多10 token偏差，而当前预算实现按参数值严格判断 | A 参数；如迁移需 B 技术处理：[思考](https://help.aliyun.com/en/model-studio/deep-thinking)、[Chat API](https://help.aliyun.com/zh/model-studio/qwen-api-via-openai-chat-completions/) |
| QWEN-USAGE | cached_input_path=prompt_tokens_details.cached_tokens；缺失策略 unknown，未宣称缺失=0。图片属于 prompt 总量，reasoning 属于 completion 输出明细，不二次计费。标准纯文本/图片Chat、不开付费内置工具/显式缓存/音视频时，填写 all_charges_in_token_usage=true、reasoning_in_output=true、usage_contract.verified=true | A：该 verified 仅表示此受限文档契约有依据，[响应字段](https://help.aliyun.com/zh/model-studio/qwen-api-via-openai-chat-completions/)、[隐式缓存](https://help.aliyun.com/en/model-studio/context-cache)、[模型价格](https://help.aliyun.com/en/model-studio/qwen3-8-max)；仍无账户与实测证明 |
| 图像及请求 | 旧域名体积16777216字节；base64最多250张。原始图本地上限20000000字节，Data URI的20MB额度扣PNG前缀22字节后，编码部分上限19999978。像素33177600来自8K建议值，仅本地保护，仍须独立满足边长/比例条件 | A候选限值：[视觉指南](https://help.aliyun.com/en/model-studio/vision) |
| 默认图像token | 默认max_pixels=2621440，32×32像素/token，再加两个视觉边界token，填auto=2562；仅适用当前未设置detail/高分辨率/像素覆盖的请求。其他detail不猜填。文档示例算法标为近似，因此该分量保留待技术确认，不能由它推导整体verified | A公式/B严格证明：[视觉指南](https://help.aliyun.com/en/model-studio/vision) |

## 共通：严格预算还缺什么

三模型的 text_tokens_per_utf8_byte、message_overhead_tokens、content_part_overhead_tokens、request_overhead_tokens 均保持 null。本轮未找到匹配线上具体模型与消息模板版本的、可证明所有允许输入成立的上界。字节数、经验汉字比例、样例token数不是这样的证明。

最小技术解决办法是取得供应商认可的精确 tokenizer/消息模板/图像预处理版本及界限，或供应商书面说明可用保守上界；对允许字符与消息结构证明后才填值。若只能获得官方分词接口，应先确认无生成/计费与适用范围，并另行批准；本轮未调用。单次模型测试只能检验观测行为，不证明全局上界。

五项费用分量已尽可能填写。GLM仅output=0.049152，其余未确认；Ark input=0.393216、output=0.245760、reasoning=0、images=0、other=null；Qwen input=0.786432、output=0.294912、reasoning=0、images=0、other=0。输入分量按65536×未缓存价；图像计入input，reasoning计入output时独立项为0。**这些是条件算术，不是已验证预留**：完整输入估计与允许收费范围未闭合前不允许发送。现金预留、用量核算及发票/优惠不得混为一谈。

C类仅剩：账户访问/额度与真实模型身份，JSON/图片观测行为，关闭思考和用量返回的实际符合性。语义校准另按原有协议验收，不由接口成功替代。
